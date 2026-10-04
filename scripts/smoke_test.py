#!/usr/bin/env python3
"""端到端冒烟测试。桩模型跑在同进程的线程里，全链路走真实 HTTP 接口。

  python3 scripts/smoke_test.py

覆盖：上传 -> SSE 分段进度 -> 分割量化 -> 桩模型判读 -> 误检剔除重算 -> 仲裁 -> 结果与资源。
另外单独验证模型不可用时的降级路径。
"""
import asyncio, json, os, shutil, sys, tempfile, threading, time
from http.server import HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

PORT = 8199
os.environ["LLM_BASE_URL"] = f"http://127.0.0.1:{PORT}/v1"
# 测试数据放系统临时目录：不往项目里留垃圾，也避开网络盘 / 共享挂载
# （SQLite 在那些文件系统上拿不到文件锁，会直接 disk I/O error）
TMP = tempfile.mkdtemp(prefix="mould-smoke-")
os.environ["STORAGE_DIR"] = TMP

import httpx
from mock_llm import H

FAILED = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if detail else ""))
    if not cond:
        FAILED.append(name)


def start_mock():
    srv = HTTPServer(("127.0.0.1", PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.4)
    return srv


async def run() -> None:
    from app.main import app
    transport = httpx.ASGITransport(app=app)
    ref = ROOT / "reference"

    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=120) as c:
        print("\n[1] 健康检查与前端配置")
        r = await c.get("/api/v1/health")
        check("health 可达", r.status_code == 200, r.json().get("status", ""))
        check("桩模型连通", r.json().get("llm_reachable") is True)
        r = await c.get("/api/v1/config")
        cfg = r.json()
        check("配置返回 5 个分级", len(cfg.get("levels", [])) == 5, cfg.get("grading_version", ""))

        print("\n[2] 完整分析（白光 + UV + 桩模型）")
        files = {"white": ("w.jpg", (ref / "pic_l3.jpg").read_bytes(), "image/jpeg"),
                 "uv": ("u.jpg", (ref / "uv_l3.jpg").read_bytes(), "image/jpeg")}
        r = await c.post("/api/v1/analyze", files=files, data={"locale": "zh"})
        check("上传返回 202", r.status_code == 202, str(r.status_code))
        task = r.json()["task_id"]

        stages = []
        result = None
        async with c.stream("GET", f"/api/v1/tasks/{task}/stream") as resp:
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                d = json.loads(line[6:])
                stages.append(f"{d['stage']}:{d['state']}")
                if d.get("result"):
                    result = d["result"]
        check("SSE 收到全部 6 段", len([s for s in stages if s.endswith(":done")]) >= 6,
              " ".join(stages))
        check("拿到最终结果", result is not None)
        if result is None:
            return

        g, m = result["grade"], result["metrics"]
        check("分级 = L3", g["level"] == 3, f"L{g['level']} {g['label_zh']}")
        check("算法与模型一致", g["source"] == "consensus", g["source"])
        check("置信度合理", 0.8 <= g["confidence"] <= 1.0, str(g["confidence"]))
        check("CV/LLM 判级都记录了", g["cv_level"] == 3 and g["llm_level"] == 3)
        check("有中文判读理由", bool(g["rationale_zh"]))

        check("白光覆盖率有值", m["coverage_white_pct"] > 0, f"{m['coverage_white_pct']}%")
        check("UV 覆盖率有值", (m["coverage_uv_pct"] or 0) > 0, f"{m['coverage_uv_pct']}%")
        check("隐性扩散倍数有值", (m["hidden_spread_ratio"] or 0) > 1, f"{m['hidden_spread_ratio']}x")
        check("无标定卡时 UV 标为估算", m["uv_quantification"] == "estimated")
        check("L3 时计数被判为无意义", m["count_is_meaningful"] is False)

        vs = [x["verdict"] for x in result["regions"]]
        check("误检区域已从结果剔除", "stain_not_mould" not in vs,
              f"{len(result['regions'])} 个区域保留")
        check("区域带活跃度标签", all(x["activity"] for x in result["regions"]))
        check("区域有多边形轮廓", all(len(x["polygon"]) >= 3 for x in result["regions"]))

        check("形态学倾向不含菌种名",
              result["morphology_hint"] is not None
              and not any(k in result["morphology_hint"]["description_zh"]
                          for k in ("葡萄穗霉", "青霉", "曲霉", "枝孢")))
        check("形态学置信度为 low/medium",
              result["morphology_hint"]["confidence"] in ("low", "medium"))
        check("有成因假设", len(result["cause_hypotheses"]) >= 1)
        check("有处置建议", len(result["actions"]) >= 1)
        check("审计信息完整",
              all(result["audit"].get(k) for k in ("algo_version", "grading_version", "model")),
              f"{result['audit']['elapsed_ms']}ms")
        check("两张图已对齐", result["audit"]["registered"] is True,
              result["audit"]["registration_method"])

        print("\n[3] 图像资源")
        for key in ("white", "uv", "overlay", "mask"):
            url = result["assets"].get(key)
            rr = await c.get(url) if url else None
            check(f"资源 {key} 可访问", rr is not None and rr.status_code == 200,
                  f"{len(rr.content)//1024}KB" if rr else "缺失")
        rr = await c.get(f"/api/v1/tasks/{task}/assets/..%2F..%2Ftasks.db")
        check("阻止目录穿越", rr.status_code == 404, str(rr.status_code))

        print("\n[4] 仅白光（无 UV）")
        r = await c.post("/api/v1/analyze",
                         files={"white": ("w.jpg", (ref / "pic_l1.jpg").read_bytes(), "image/jpeg")})
        t2 = r.json()["task_id"]
        async with c.stream("GET", f"/api/v1/tasks/{t2}/stream") as resp:
            r2 = None
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    d = json.loads(line[6:])
                    if d.get("result"):
                        r2 = d["result"]
        check("无 UV 也能出结果", r2 is not None and r2["grade"] is not None)
        if r2:
            check("无 UV 时置信度被封顶", r2["grade"]["confidence"] <= 0.60,
                  str(r2["grade"]["confidence"]))
            check("无 UV 时给出明确提示",
                  any("UV" in n for n in r2["notes_zh"]))
            check("L1 时计数有意义", r2["metrics"]["count_is_meaningful"] is True)

        print("\n[5] 模型不可用时降级")
        from app.config import get_settings
        s = get_settings()
        orig = s.llm_base_url
        s.llm_base_url = "http://127.0.0.1:1/v1"
        try:
            r = await c.post("/api/v1/analyze", files={
                "white": ("w.jpg", (ref / "pic_l5.jpg").read_bytes(), "image/jpeg"),
                "uv": ("u.jpg", (ref / "uv_l5.jpg").read_bytes(), "image/jpeg")})
            t3 = r.json()["task_id"]
            async with c.stream("GET", f"/api/v1/tasks/{t3}/stream") as resp:
                r3 = None
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        d = json.loads(line[6:])
                        if d.get("result"):
                            r3 = d["result"]
            check("模型挂掉仍出分级", r3 is not None and r3["grade"] is not None,
                  f"L{r3['grade']['level']}" if r3 else "")
            check("标记为仅算法判定", r3 and r3["grade"]["source"] == "cv_only")
            check("L5 判对", r3 and r3["grade"]["level"] == 5)
        finally:
            s.llm_base_url = orig

        print("\n[6] 质量拦截与删除")
        import numpy as np, cv2
        blank = cv2.imencode(".jpg", np.full((900, 1200, 3), 12, np.uint8))[1].tobytes()
        r = await c.post("/api/v1/analyze", files={"white": ("b.jpg", blank, "image/jpeg")})
        t4 = r.json()["task_id"]
        for _ in range(40):
            rr = await c.get(f"/api/v1/tasks/{t4}")
            if rr.status_code != 202:
                break
            await asyncio.sleep(0.25)
        check("过暗图像被拦截", rr.json().get("status") == "rejected",
              str(rr.json().get("reason_code")))
        rr = await c.delete(f"/api/v1/tasks/{task}")
        check("可删除记录", rr.json().get("deleted") is True)
        rr = await c.get(f"/api/v1/tasks/{task}")
        check("删除后返回 404", rr.status_code == 404)


if __name__ == "__main__":
    start_mock()
    try:
        asyncio.run(run())
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print("\n" + "=" * 46)
    if FAILED:
        print(f"失败 {len(FAILED)} 项: " + ", ".join(FAILED))
        raise SystemExit(1)
    print("全部通过")

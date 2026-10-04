#!/usr/bin/env python3
"""最小 OpenAI 兼容多模态桩服务，用于在没有真实模型时跑通全链路。

  python3 scripts/mock_llm.py 8100
然后把 .env 的 LLM_BASE_URL 指向 http://127.0.0.1:8100/v1

它会读出请求里的区域编号并生成一份格式合法的判读结果，
用于验证解析、仲裁、误检剔除与前端渲染，不做任何真实判断。
"""
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


def build(req: dict) -> dict:
    ids, texts = [], []
    for m in req.get("messages", []):
        c = m.get("content")
        if isinstance(c, list):
            for part in c:
                if part.get("type") == "text":
                    txt = part.get("text", "")
                    texts.append(txt)
                    ids += [int(x) for x in re.findall(r"#(\d+)：占画面", txt)]
    ids = sorted(set(ids))[:12]
    level = _level_from_prompt(" ".join(texts))

    regions = []
    for i, rid in enumerate(ids):
        # 故意把最后一个标为误检，用来验证"剔除后重算"这条路径
        if i == len(ids) - 1 and len(ids) > 2:
            regions.append({"id": rid, "verdict": "stain_not_mould", "activity": "unknown",
                            "uv_response": "none",
                            "note_zh": "边界锐利、无荧光响应，更像陈旧水渍",
                            "note_en": "Sharp edge, no UV response — looks like an old water stain"})
        else:
            regions.append({"id": rid, "verdict": "mould",
                            "activity": "active" if i < 2 else "dormant",
                            "uv_response": "strong_halo" if i < 2 else "weak",
                            "note_zh": "荧光边界外扩，提示仍在扩散" if i < 2 else "无明显外扩",
                            "note_en": "Halo extends beyond the visible stain" if i < 2 else "No visible spread"})

    return {
        "is_wall_surface": True,
        "reason_code": None,
        "grade": {"level": level, "confidence": 0.81,
                  "rationale_zh": "存在深色致密菌落与圆形卫星斑，UV 下荧光边界显著外扩，符合中度判据。",
                  "rationale_en": "Dark dense colonies with satellite spots; UV halo extends well beyond the stain."},
        "regions": regions,
        "morphology_hint": {
            "description_zh": "深色致密孢子团簇，边缘呈绒毛状，与产黑色素类真菌形态一致。",
            "description_en": "Dark dense spore clusters with felted margins, consistent with melanised fungi.",
            "confidence": "low"},
        "cause_hypotheses": [
            {"cause_zh": "外墙渗漏", "cause_en": "External wall leak", "likelihood": 0.6,
             "evidence_zh": "分布自上而下，边界呈水痕状", "evidence_en": "Top-down distribution with tide-line edges"},
            {"cause_zh": "冷桥冷凝", "cause_en": "Thermal bridge condensation", "likelihood": 0.3,
             "evidence_zh": "集中于墙体交接处", "evidence_en": "Concentrated at the junction"}],
        "extra_actions": [
            {"priority": "high", "text_zh": "7 日内查明并切断水源，否则清除后必然复发。",
             "text_en": "Find and stop the water within 7 days, or it will return after cleaning."},
            {"priority": "medium", "text_zh": "处理范围应比可见污渍外扩至少 30cm。",
             "text_en": "Treat at least 30 cm beyond the visible stain."}],
    }


def _level_from_prompt(text: str) -> int:
    """桩模型按 prompt 里的实测白光覆盖率给级，模拟一个判断合理的模型。
    否则它总回同一个级别，冒烟测试就测不出仲裁逻辑。"""
    m = re.search(r"白光覆盖率：([\d.]+)%", text)
    cov = float(m.group(1)) if m else 5.0
    for lvl, hi in ((1, 3.0), (2, 8.0), (3, 25.0), (4, 60.0)):
        if cov < hi:
            return lvl
    return 5


class H(BaseHTTPRequestHandler):
    def _send(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.endswith("/models"):
            self._send({"data": [{"id": "mock-vl"}]})
        else:
            self._send({"error": "not found"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n) or b"{}")
        imgs = sum(1 for m in req.get("messages", [])
                   for p in (m.get("content") if isinstance(m.get("content"), list) else [])
                   if p.get("type") == "image_url")
        print(f"[mock] model={req.get('model')} images={imgs} json_mode={'response_format' in req}")
        self._send({
            "id": "mock-001", "object": "chat.completion", "model": req.get("model", "mock-vl"),
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant",
                                     "content": json.dumps(build(req), ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        })

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8100
    print(f"桩模型服务已启动: http://127.0.0.1:{port}/v1")
    HTTPServer(("127.0.0.1", port), H).serve_forever()

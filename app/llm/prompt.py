"""Prompt 构造。

三条原则：
  1. 给证据，不让它猜 —— CV 已测出的数字写进 prompt，模型基于实测判读。
  2. 限定职责     —— 它审掩膜、定分级、推成因，不测量、不鉴定菌种。
  3. 不确定性结构化 —— 拿不准就填字段（uncertain / reason_code），不写免责话术。
"""
from __future__ import annotations

import base64
import functools
from pathlib import Path
from typing import Any, Dict, List, Optional

import cv2

from ..cv.segment import Metrics

ANCHOR_LEVELS = [1, 3, 5]      # anchor 模式注入的锚点：两端 + 中位

SYSTEM = """你是建筑病理与室内环境检测领域的图像判读助手，负责墙面霉菌的筛查性分级。

硬性要求：
1. 你不做菌种鉴定。任何情况下都不得给出属或种的确定性结论（如"这是葡萄穗霉"）。
   只能在 morphology_hint 中描述形态学倾向，并且 confidence 只能是 low 或 medium。
2. 你不提供医疗或健康诊断建议。
3. 你不测量。覆盖率、面积、数量等数值已由图像算法测出并在输入中给你，
   你的任务是基于这些实测值与图像做判读，不要自行估算或改写这些数字。
4. 区域坐标已由算法给出并标注在叠加图上。你只针对已编号的区域作判断，
   不要输出新的坐标或边界框。
5. 只输出 JSON 对象，不要 Markdown 代码块，不要任何解释性前后缀。
6. 中文字段用简体中文，英文字段用英文，两者表达同一内容但各自符合语言习惯。"""

SCHEMA = """{
  "is_wall_surface": true,
  "reason_code": null,
  "grade": {
    "level": 1-5,
    "confidence": 0.0-1.0,
    "rationale_zh": "一到两句，指出决定性的视觉证据",
    "rationale_en": "..."
  },
  "regions": [
    {
      "id": 区域编号（必须来自输入的编号，不要新增）,
      "verdict": "mould" | "stain_not_mould" | "uncertain",
      "activity": "active" | "dormant" | "unknown",
      "uv_response": "strong_halo" | "weak" | "none" | "unknown",
      "note_zh": "简短说明", "note_en": "..."
    }
  ],
  "morphology_hint": {
    "description_zh": "形态学描述，不得含菌种名称",
    "description_en": "...",
    "confidence": "low" | "medium"
  },
  "cause_hypotheses": [
    {"cause_zh": "", "cause_en": "", "likelihood": 0.0-1.0, "evidence_zh": "", "evidence_en": ""}
  ],
  "extra_actions": [
    {"priority": "high" | "medium" | "low", "text_zh": "", "text_en": ""}
  ]
}"""


def _b64(img_bytes: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(img_bytes).decode("ascii")


def _img_part(img_bytes: bytes) -> Dict[str, Any]:
    return {"type": "image_url", "image_url": {"url": _b64(img_bytes)}}


def _text(t: str) -> Dict[str, Any]:
    return {"type": "text", "text": t}


@functools.lru_cache(maxsize=16)
def _anchor_bytes(path: str, max_edge: int) -> Optional[bytes]:
    img = cv2.imread(path)
    if img is None:
        return None
    m = max(img.shape[:2])
    if m > max_edge:
        s = max_edge / m
        img = cv2.resize(img, (int(img.shape[1] * s), int(img.shape[0] * s)),
                         interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 82])
    return buf.tobytes() if ok else None


def criteria_text(cfg: Dict[str, Any]) -> str:
    lines = ["分级标准（文字判据为主要依据，数值区间为参考）："]
    for lv in cfg["levels"]:
        t = lv["thresholds"]
        lines.append(
            f"L{lv['level']} {lv['label_zh']} / {lv['label_en']}："
            f"{' '.join(lv['criteria_zh'].split())}"
            f" 参考区间：白光覆盖率 {t['coverage_white_pct'][0]}–{t['coverage_white_pct'][1]}%，"
            f"最大连片 {t['largest_patch_pct'][0]}–{t['largest_patch_pct'][1]}%。"
        )
    return "\n".join(lines)


def measurements_text(m: Metrics, registered: bool, has_uv: bool) -> str:
    lines = ["图像算法已测出的量化结果（这些是事实，请直接采用，不要改写）："]
    lines.append(f"- 白光覆盖率：{m.coverage_white_pct}%")
    if m.coverage_uv_pct is not None:
        tag = "已用标定卡归一，可信" if m.uv_quantification == "calibrated" else "无标定卡，为估算值"
        lines.append(f"- UV 覆盖率：{m.coverage_uv_pct}%（{tag}）")
    if m.hidden_spread_ratio is not None:
        lines.append(f"- 隐性扩散倍数（UV/白光）：{m.hidden_spread_ratio}×")
    lines.append(f"- 最大连片面积占比：{m.largest_patch_pct}%")
    lines.append(f"- 斑块数：{m.patch_count}")
    if m.absolute_area_cm2:
        lines.append(f"- 绝对受影响面积：约 {m.absolute_area_cm2} cm²")
    if not has_uv:
        lines.append("- 注意：本次未提供 UV 图，浅色霉菌可能被严重低估。")
    elif not registered:
        lines.append("- 注意：两张图未能自动对齐，差异类指标仅供参考。")

    lines.append("\n算法圈出的候选区域（叠加图上已标编号）：")
    for r in m.regions:
        halo = "，UV 下有荧光外扩" if r.uv_halo else ""
        lines.append(f"  #{r.id}：占画面 {r.area_pct}%，暗度 {r.mean_darkness}{halo}")
    return "\n".join(lines)


def build_messages(cfg: Dict[str, Any], settings, m: Metrics,
                   white_jpeg: bytes, uv_jpeg: Optional[bytes], overlay_jpeg: bytes,
                   registered: bool, notes: str = "") -> List[Dict[str, Any]]:
    content: List[Dict[str, Any]] = []

    content.append(_text(criteria_text(cfg)))

    mode = (settings.reference_mode or "none").lower()
    if mode in ("anchor", "full"):
        wanted = ANCHOR_LEVELS if mode == "anchor" else [1, 2, 3, 4, 5]
        content.append(_text(
            f"以下是 {len(wanted)} 组已标定分级的参考图，用于校准你对严重程度的判断尺度："
        ))
        ref_dir: Path = settings.reference_path
        for lvl in wanted:
            uv_b = _anchor_bytes(str(ref_dir / f"uv_l{lvl}.jpg"), settings.reference_max_edge)
            if uv_b is None:
                continue
            content.append(_text(f"【参考】L{lvl} 级 · UV 图"))
            content.append(_img_part(uv_b))
            if mode == "full":
                w_b = _anchor_bytes(str(ref_dir / f"pic_l{lvl}.jpg"), settings.reference_max_edge)
                if w_b:
                    content.append(_text(f"【参考】L{lvl} 级 · 白光图"))
                    content.append(_img_part(w_b))

    content.append(_text("以下是本次待判读的现场图像。"))
    content.append(_text("【待判读 1/3】白光图"))
    content.append(_img_part(white_jpeg))
    if uv_jpeg:
        content.append(_text("【待判读 2/3】365nm UV 图"))
        content.append(_img_part(uv_jpeg))
    content.append(_text("【待判读 3/3】算法掩膜叠加图（青色区域为算法圈出的候选霉斑，方框内数字为区域编号）"))
    content.append(_img_part(overlay_jpeg))

    content.append(_text(measurements_text(m, registered, uv_jpeg is not None)))
    if notes.strip():
        content.append(_text(f"用户补充说明：{notes.strip()[:500]}"))

    content.append(_text(
        "任务：\n"
        "1. 逐个审核算法圈出的编号区域，判断哪些确为霉菌、哪些是水渍/盐析/涂层剥落/阴影等误检。\n"
        "2. 综合图像与上述实测值给出 L1–L5 分级与置信度。\n"
        "3. 给出形态学倾向（不得含菌种名称）、成因假设、以及标准处置建议之外的补充措施。\n"
        "4. 若图像主体不是墙面或无法判断，令 is_wall_surface=false 并填写 reason_code"
        "（not_a_wall / too_dark / too_blurry / obstructed），此时 grade 可为 null。\n\n"
        "严格按以下 JSON 结构输出：\n" + SCHEMA
    ))

    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": content},
    ]

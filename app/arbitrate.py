"""仲裁：把 CV 阈值判级与大模型判级合成一个可解释的结论。

策略由 GRADING_STRATEGY 决定：
  llm_led (默认) —— 大模型主导。它拿到完整文字判据、参考图锚点和 CV 实测值，
                    比死阈值更能应对不同墙面材质与拍摄条件。CV 用于交叉验证。
  cv_led         —— CV 阈值主导，大模型仅做区域校验。适合模型不稳定的环境。

无论哪种策略，报告上的数字始终来自 CV。分级差异被显式呈现而不是悄悄取平均。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .cv.segment import Metrics


@dataclass
class Verdict:
    level: int
    confidence: float
    source: str                       # consensus | llm | cv_only | needs_review
    cv_level: Optional[int] = None
    llm_level: Optional[int] = None
    notes_zh: List[str] = None
    notes_en: List[str] = None


def _in_range(v: float, rng: List[float]) -> bool:
    return rng[0] <= v < rng[1] or (rng[1] >= 100.0 and v >= rng[0])


def grade_from_metrics(m: Metrics, cfg: Dict[str, Any]) -> int:
    """CV 兜底判级：白光覆盖率定主级，最大连片度可上调不可下调。

    只用白光是刻意的 —— 无标定卡时 UV 覆盖率不可信，不能让它参与定级。
    """
    levels = cfg["levels"]
    by_cov = 1
    for lv in levels:
        if _in_range(m.coverage_white_pct, lv["thresholds"]["coverage_white_pct"]):
            by_cov = lv["level"]
            break
    else:
        by_cov = levels[-1]["level"] if m.coverage_white_pct >= levels[-1]["thresholds"]["coverage_white_pct"][0] else 1

    by_patch = 1
    for lv in levels:
        if _in_range(m.largest_patch_pct, lv["thresholds"]["largest_patch_pct"]):
            by_patch = lv["level"]
            break

    # 连片度只用于上调：一大片连续菌毯即使总覆盖率不高也是重度问题
    return max(by_cov, by_patch)


def arbitrate(m: Metrics, llm_level: Optional[int], llm_conf: Optional[float],
              cfg: Dict[str, Any], strategy: str,
              has_uv: bool, registered: bool) -> Verdict:
    rules = cfg.get("arbitration", {})
    cv_level = grade_from_metrics(m, cfg)
    notes_zh: List[str] = []
    notes_en: List[str] = []

    if llm_level is None:
        v = Verdict(level=cv_level, confidence=0.55, source="cv_only",
                    cv_level=cv_level, llm_level=None)
        notes_zh.append("大模型判读不可用，本结论仅由图像算法得出，建议人工复核。")
        notes_en.append("Model interpretation unavailable; result is algorithm-only. Manual review advised.")
    else:
        diff = abs(llm_level - cv_level)
        base = float(llm_conf if llm_conf is not None else 0.7)
        if diff == 0:
            level = llm_level
            conf = min(0.98, base + float(rules.get("agree_confidence_bonus", 0.10)))
            source = "consensus"
        elif diff == 1:
            level = max(llm_level, cv_level)
            conf = max(0.35, base - float(rules.get("diff1_penalty", 0.15)))
            source = "llm" if strategy == "llm_led" else "cv_only"
            notes_zh.append(f"算法判定 L{cv_level}，大模型判定 L{llm_level}，相差一级，取较高者。")
            notes_en.append(f"Algorithm said L{cv_level}, model said L{llm_level}; the higher grade is used.")
        else:
            level = llm_level if strategy == "llm_led" else cv_level
            conf = 0.4
            source = "needs_review" if rules.get("diff2_needs_review", True) else source_default(strategy)
            notes_zh.append(f"算法与大模型判定相差 {diff} 级（L{cv_level} / L{llm_level}），需人工复核。")
            notes_en.append(f"Algorithm and model differ by {diff} grades (L{cv_level} / L{llm_level}). Manual review required.")
        v = Verdict(level=level, confidence=round(conf, 2), source=source,
                    cv_level=cv_level, llm_level=llm_level)

    # 证据不足时给置信度封顶——这些上限不可被模型的自信覆盖
    if not has_uv:
        cap = float(rules.get("no_uv_confidence_cap", 0.60))
        if v.confidence > cap:
            v.confidence = cap
        notes_zh.append("未提供 UV 图。白色与浅色霉菌在白光下极难识别，实际情况可能更严重。")
        notes_en.append("No UV frame provided. Pale moulds are hard to see under white light; the true extent may be worse.")
    elif not registered:
        cap = float(rules.get("unregistered_confidence_cap", 0.75))
        if v.confidence > cap:
            v.confidence = cap
        notes_zh.append("两张图未能自动对齐，差异类指标（隐性扩散倍数）仅供参考。")
        notes_en.append("The two frames could not be aligned; difference metrics are indicative only.")

    if m.uv_quantification == "estimated":
        notes_zh.append("未检出标定卡，UV 覆盖率为估算值，不作为结论性数字。")
        notes_en.append("No calibration card detected — UV coverage is an estimate, not a conclusive figure.")

    hs = cfg.get("hidden_spread", {})
    if (m.hidden_spread_ratio or 0) >= float(hs.get("alert_ratio", 2.5)) \
            and m.coverage_white_pct < float(hs.get("meaningful_below_white_pct", 30.0)):
        notes_zh.append(f"UV 下可见范围是肉眼的 {m.hidden_spread_ratio}倍，提示存在活跃扩散，处理范围应大于可见污渍。")
        notes_en.append(f"UV reveals {m.hidden_spread_ratio}x the visible area — treat well beyond the visible stain.")

    v.notes_zh, v.notes_en = notes_zh, notes_en
    return v


def source_default(strategy: str) -> str:
    return "llm" if strategy == "llm_led" else "cv_only"

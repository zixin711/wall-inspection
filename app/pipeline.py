"""六段流水线的编排。

进度通过 asyncio.Queue 推给 SSE：CV 结果约 2 秒可见，大模型判读随后补上，
用户不会盯着白屏。大模型失败时整条链降级为纯 CV 结果，仍然出分级。
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict
from typing import Any, Callable, Dict, List, Optional

import cv2
import numpy as np

from .arbitrate import arbitrate, grade_from_metrics
from .config import get_settings, grading_config, level_by_number
from .cv import intake, registration, render
from .cv import segment as seg
from .cv.calib import Calibration, detect as detect_card
from .cv.segment import Metrics
from .llm.client import VisionClient
from .llm.prompt import build_messages
from .schemas import (ActionItem, AnalysisResult, AuditOut, CauseHypothesis, GradeOut,
                      LLMResult, MetricsOut, MorphologyHint, RegionOut)
from .store import Store, utcnow

log = logging.getLogger("mould.pipeline")
ALGO_VERSION = "cv-1.0.0"

Progress = Callable[[str, str, Dict[str, Any]], Any]


async def _emit(cb: Optional[Progress], stage: str, state: str, **data: Any) -> None:
    if cb is None:
        return
    res = cb(stage, state, data)
    if asyncio.iscoroutine(res):
        await res


async def run(task_id: str, white_bytes: bytes, uv_bytes: Optional[bytes],
              store: Store, notes: str = "", on_progress: Optional[Progress] = None) -> Dict[str, Any]:
    s = get_settings()
    cfg = grading_config()
    t0 = time.perf_counter()

    # ---------------------------------------------------------- 01 预检
    await _emit(on_progress, "intake", "start")
    white = intake.decode(white_bytes)
    if white is None:
        return _rejected(task_id, "decode_failed",
                         "无法解析白光图，请换用 JPEG 或 PNG 格式。",
                         "Could not decode the white-light image. Use JPEG or PNG.")
    uv = intake.decode(uv_bytes) if uv_bytes else None

    white = intake.downscale(white, s.analysis_long_edge)
    if uv is not None:
        uv = intake.downscale(uv, s.analysis_long_edge)

    q_issues: List[Dict[str, str]] = []
    rep_w = intake.check(white, "white")
    q_issues += [{"code": i.code, "severity": i.severity, "zh": i.zh, "en": i.en} for i in rep_w.issues]
    blocked = not rep_w.ok
    if uv is not None:
        rep_u = intake.check(uv, "uv")
        q_issues += [{"code": i.code, "severity": i.severity, "zh": i.zh, "en": i.en} for i in rep_u.issues]
        blocked = blocked or not rep_u.ok
        pc = intake.pair_consistency(white, uv)
        if pc:
            q_issues.append({"code": pc.code, "severity": pc.severity, "zh": pc.zh, "en": pc.en})

    if blocked:
        out = _rejected(task_id, "quality_blocked",
                        "图像质量不满足分析要求，请按提示重拍。",
                        "Image quality is insufficient. Please retake as advised.")
        out["quality_issues"] = q_issues
        store.finish(task_id, "rejected", out)
        await _emit(on_progress, "intake", "rejected", issues=q_issues)
        return out
    await _emit(on_progress, "intake", "done", issues=q_issues)

    # ---------------------------------------------------------- 02 配准
    registered, method = False, "none"
    if uv is not None:
        await _emit(on_progress, "register", "start")
        if s.enable_registration:
            uv, registered, method = registration.register(uv, white)
        else:
            uv = cv2.resize(uv, (white.shape[1], white.shape[0]))
        await _emit(on_progress, "register", "done", registered=registered, method=method)

    # ---------------------------------------------------------- 03 分割量化
    await _emit(on_progress, "quantify", "start")
    calib: Optional[Calibration] = None
    if s.enable_calibration_card:
        calib = detect_card(uv if uv is not None else white)
        if not calib.found and uv is not None:
            calib = detect_card(white)
    metrics, wmask, umask = seg.analyse(white, uv, calib, cfg)
    cv_level = grade_from_metrics(metrics, cfg)
    seg.apply_count_rule(metrics, cv_level, cfg)

    overlay_user = render.overlay(uv if uv is not None else white, umask if umask is not None else wmask,
                                  metrics.regions, label=False)
    overlay_llm = render.overlay(uv if uv is not None else white, umask if umask is not None else wmask,
                                 metrics.regions, label=True)

    assets = {
        "white": store.put_asset(task_id, "white.jpg", render.encode_jpeg(white, 84, 1600)),
        "overlay": store.put_asset(task_id, "overlay.jpg", render.encode_jpeg(overlay_user, 84, 1600)),
        "mask": store.put_asset(task_id, "mask.png", render.encode_mask_png(umask if umask is not None else wmask)),
    }
    if uv is not None:
        assets["uv"] = store.put_asset(task_id, "uv.jpg", render.encode_jpeg(uv, 84, 1600))

    await _emit(on_progress, "quantify", "done",
                preliminary={"cv_level": cv_level, "metrics": _metrics_payload(metrics, cfg), "assets": assets})

    # ---------------------------------------------------------- 04 大模型判读
    await _emit(on_progress, "interpret", "start")
    llm_result: Optional[LLMResult] = None
    llm_audit: Dict[str, Any] = {}
    raw_llm: Optional[Dict[str, Any]] = None
    try:
        client = VisionClient(s)
        messages = build_messages(
            cfg, s, metrics,
            render.encode_jpeg(white, 82, 1024),
            render.encode_jpeg(uv, 82, 1024) if uv is not None else None,
            render.encode_jpeg(overlay_llm, 82, 1024),
            registered, notes,
        )
        raw_llm, llm_audit = await client.complete(messages)
        if raw_llm is not None:
            llm_result = LLMResult.model_validate(raw_llm)
    except Exception as exc:                       # 判读失败绝不拖垮整个请求
        log.exception("大模型判读异常")
        llm_audit = {"error": f"{type(exc).__name__}: {exc}"}

    await _emit(on_progress, "interpret", "done" if llm_result else "degraded",
                error=llm_audit.get("error"))

    # 模型判定不是墙面 —— 直接退回，不硬套分级
    if llm_result is not None and not llm_result.is_wall_surface:
        out = _rejected(task_id, llm_result.reason_code or "not_a_wall",
                        "图像主体似乎不是墙面，或条件不足以判断。请对准墙面重拍。",
                        "The frame does not appear to show a wall surface. Please retake.")
        out["assets"] = assets
        out["quality_issues"] = q_issues
        store.finish(task_id, "rejected", out, raw_llm)
        return out

    # ---------------------------------------------------------- 05 仲裁
    await _emit(on_progress, "arbitrate", "start")
    llm_level = llm_result.grade.level if (llm_result and llm_result.grade) else None
    llm_conf = llm_result.grade.confidence if (llm_result and llm_result.grade) else None

    # 模型判为误检的区域从掩膜中剔除后重算数字
    dropped = {r.id for r in (llm_result.regions if llm_result else []) if r.verdict == "stain_not_mould"}
    if dropped:
        metrics = _recompute_without(metrics, dropped, cfg, calib)

    verdict = arbitrate(metrics, llm_level, llm_conf, cfg, s.grading_strategy,
                        has_uv=uv is not None, registered=registered)
    seg.apply_count_rule(metrics, verdict.level, cfg)
    await _emit(on_progress, "arbitrate", "done", level=verdict.level)

    # ---------------------------------------------------------- 06 组装
    lv = level_by_number(verdict.level) or {}
    actions: List[ActionItem] = [ActionItem(
        priority="high" if verdict.level >= 3 else "medium",
        text_zh=lv.get("action_zh", ""), text_en=lv.get("action_en", ""),
    )]
    if llm_result:
        actions += llm_result.extra_actions[:4]

    region_map = {r.id: r for r in (llm_result.regions if llm_result else [])}
    regions_out: List[RegionOut] = []
    for r in metrics.regions:
        if r.id in dropped:
            continue
        lr = region_map.get(r.id)
        regions_out.append(RegionOut(
            id=r.id, bbox=list(r.bbox), polygon=[list(p) for p in r.polygon],
            area_pct=r.area_pct,
            area_cm2=(calib.area_cm2(r.area_pct / 100.0 * white.shape[0] * white.shape[1])
                      if calib and calib.found else None),
            verdict=lr.verdict if lr else "uncertain",
            activity=lr.activity if lr else ("active" if r.uv_halo else "unknown"),
            uv_response=lr.uv_response if lr else ("strong_halo" if r.uv_halo else "unknown"),
            note_zh=lr.note_zh if lr else "", note_en=lr.note_en if lr else "",
        ))

    result = AnalysisResult(
        task_id=task_id, status="done", created_at=utcnow(),
        grade=GradeOut(
            level=verdict.level, key=lv.get("key", ""),
            label_zh=lv.get("label_zh", ""), label_en=lv.get("label_en", ""),
            confidence=verdict.confidence, source=verdict.source,
            cv_level=verdict.cv_level, llm_level=verdict.llm_level,
            rationale_zh=(llm_result.grade.rationale_zh if (llm_result and llm_result.grade) else ""),
            rationale_en=(llm_result.grade.rationale_en if (llm_result and llm_result.grade) else ""),
        ),
        metrics=MetricsOut(**_metrics_payload(metrics, cfg)),
        regions=regions_out,
        morphology_hint=(llm_result.morphology_hint if llm_result else None),
        cause_hypotheses=(llm_result.cause_hypotheses[:4] if llm_result else []),
        actions=actions,
        notes_zh=verdict.notes_zh or [], notes_en=verdict.notes_en or [],
        quality_issues=q_issues,
        assets=assets,
        audit=AuditOut(
            algo_version=ALGO_VERSION, grading_version=cfg.get("version", "unknown"),
            model=llm_audit.get("model"), llm_attempts=int(llm_audit.get("attempts", 0)),
            llm_error=llm_audit.get("error"), registered=registered,
            registration_method=method, has_uv=uv is not None,
            elapsed_ms=int((time.perf_counter() - t0) * 1000),
        ),
    )
    payload = result.model_dump()
    store.finish(task_id, "done", payload, raw_llm)
    await _emit(on_progress, "done", "done", result=payload)
    return payload


# ---------------------------------------------------------------- helpers

def _metrics_payload(m: Metrics, cfg: Dict[str, Any]) -> Dict[str, Any]:
    hs = cfg.get("hidden_spread", {})
    display = hs.get("display_priority", "secondary") if hs.get("enabled", True) else "hidden"
    if m.hidden_spread_ratio is None:
        display = "hidden"
    elif m.coverage_white_pct >= float(hs.get("meaningful_below_white_pct", 30.0)):
        # 覆盖率已经很高时倍数必然趋近 1，再突出它反而误导
        display = "secondary"
    return {
        "coverage_white_pct": m.coverage_white_pct,
        "coverage_uv_pct": m.coverage_uv_pct,
        "hidden_spread_ratio": m.hidden_spread_ratio,
        "hidden_spread_display": display,
        "largest_patch_pct": m.largest_patch_pct,
        "patch_count": m.patch_count,
        "count_is_meaningful": m.count_is_meaningful,
        "absolute_area_cm2": m.absolute_area_cm2,
        "scale_source": m.scale_source,
        "uv_quantification": m.uv_quantification,
    }


def _recompute_without(m: Metrics, dropped: set, cfg: Dict[str, Any],
                       calib: Optional[Calibration]) -> Metrics:
    """剔除模型判为误检的区域后重算面积类指标。"""
    kept = [r for r in m.regions if r.id not in dropped]
    removed_pct = sum(r.area_pct for r in m.regions if r.id in dropped)
    m.regions = kept
    m.coverage_white_pct = round(max(0.0, m.coverage_white_pct - removed_pct), 2)
    m.largest_patch_pct = max((r.area_pct for r in kept), default=0.0)
    m.patch_count = max(0, m.patch_count - len(dropped))
    if m.coverage_uv_pct is not None and m.coverage_white_pct > 0:
        hs = cfg.get("hidden_spread", {})
        if m.coverage_white_pct >= float(hs.get("min_white_pct_for_ratio", 0.5)):
            m.hidden_spread_ratio = round(m.coverage_uv_pct / m.coverage_white_pct, 2)
        else:
            m.hidden_spread_ratio = None
    if calib and calib.found and m.absolute_area_cm2:
        m.absolute_area_cm2 = round(m.absolute_area_cm2 * (1 - removed_pct / max(m.coverage_white_pct + removed_pct, 1e-6)), 1)
    return m


def _rejected(task_id: str, code: str, zh: str, en: str) -> Dict[str, Any]:
    return AnalysisResult(
        task_id=task_id, status="rejected", created_at=utcnow(),
        reason_code=code, notes_zh=[zh], notes_en=[en],
    ).model_dump()

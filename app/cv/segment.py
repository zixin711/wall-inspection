"""分割与量化 —— 报告上出现的每一个数字都产自这里。

方法：Lab-L 通道做大尺度平场校正（除以重度模糊的自身亮度），把手电筒渐晕
和不均匀照明变成常数场，再按"清洁墙面"高百分位取相对阈值。相对阈值配合
平场校正后在 L1→L5 上给出单调的覆盖率阶梯（实测 2.3/3.9/14.7/50.5/73.1）。

有标定卡时先用卡片做亮度与白平衡归一，UV 覆盖率才算可信；没有卡片时
UV 结果标记为 estimated，前端与报告须相应降级呈现。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import cv2
import numpy as np

from .calib import Calibration, mask_out_card

# 平场模糊核相对长边的比例：太小会把大片霉斑当成背景吃掉，太大则去不掉渐晕
FLAT_SIGMA_RATIO = 0.075
BG_PERCENTILE = 90.0          # 清洁墙面亮度参考位
K_WHITE = 0.90                # 白光：低于参考位 10% 判为霉斑/污渍
K_UV = 0.88                   # UV：紫光下对比更强，阈值略紧
MIN_BLOB_AREA_RATIO = 2e-5    # 小于此面积的连通域视为噪点


@dataclass
class Region:
    id: int
    bbox: Tuple[float, float, float, float]      # 归一化 x,y,w,h
    area_pct: float
    polygon: List[Tuple[float, float]]
    centroid: Tuple[float, float]
    mean_darkness: float                          # 相对清洁墙面的暗度 0-1
    uv_halo: bool = False                         # 是否检出荧光外扩边界


@dataclass
class Metrics:
    coverage_white_pct: float = 0.0
    coverage_uv_pct: Optional[float] = None
    hidden_spread_ratio: Optional[float] = None
    largest_patch_pct: float = 0.0
    patch_count: int = 0
    count_is_meaningful: bool = True
    absolute_area_cm2: Optional[float] = None
    scale_source: str = "none"                    # calibration_card | none
    uv_quantification: str = "unavailable"        # calibrated | estimated | unavailable
    regions: List[Region] = field(default_factory=list)


def _flat_field(img: np.ndarray) -> np.ndarray:
    """返回相对反射率图（去掉低频照明），值域约 0-2，清洁墙面≈1。"""
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    L = cv2.GaussianBlur(lab[:, :, 0].astype(np.float32), (0, 0), 1.2)
    sigma = max(12.0, FLAT_SIGMA_RATIO * max(img.shape[:2]))
    bg = np.maximum(cv2.GaussianBlur(L, (0, 0), sigma), 1.0)
    return L / bg


def _binarize(rel: np.ndarray, k: float) -> np.ndarray:
    ref = float(np.percentile(rel, BG_PERCENTILE))
    m = ((rel < ref * k) * 255).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    return m


def _clean(mask: np.ndarray, min_area: int) -> np.ndarray:
    n, lab, stats, _ = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), 8)
    out = np.zeros_like(mask)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] >= min_area:
            out[lab == i] = 255
    return out


def segment(img: np.ndarray, kind: str, calib: Optional[Calibration] = None) -> np.ndarray:
    """kind: 'white' | 'uv' —— 返回二值掩膜。"""
    work = img
    if calib is not None and calib.found:
        from .calib import normalize_with_card
        work = normalize_with_card(img, calib)
    rel = _flat_field(work)
    mask = _binarize(rel, K_WHITE if kind == "white" else K_UV)
    min_area = max(30, int(MIN_BLOB_AREA_RATIO * img.shape[0] * img.shape[1]))
    mask = _clean(mask, min_area)
    if calib is not None:
        mask = mask_out_card(mask, calib)
    return mask


def _coverage(mask: np.ndarray) -> float:
    return float((mask > 0).sum()) * 100.0 / float(mask.size)


MIN_REGION_PCT = 0.25          # 小于画面 0.25% 的斑点不单独列出（仍计入覆盖率）
MIN_REGIONS_KEPT = 3           # 但至少保留 3 个，否则清单会空


def extract_regions(mask: np.ndarray, uv_mask: Optional[np.ndarray],
                    rel: Optional[np.ndarray], max_regions: int = 8) -> List[Region]:
    """按面积取前 N 个连通域，输出归一化 bbox 与简化多边形。

    大模型拿到的是这些区域的编号叠加图，它只需回答每块是什么、活跃与否，
    不需要自己产生坐标 —— 坐标漂移问题在这里被结构性地消除了。
    """
    h, w = mask.shape[:2]
    total = float(h * w)
    n, labels, stats, cents = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), 8)
    ranked = sorted(range(1, n), key=lambda i: -stats[i, cv2.CC_STAT_AREA])

    # 列出 11 个 0.1% 的斑点对用户没有价值，反而淹没了真正要处理的那几块。
    # 小斑块仍计入覆盖率，只是不单独成行。
    big = [i for i in ranked if stats[i, cv2.CC_STAT_AREA] * 100.0 / total >= MIN_REGION_PCT]
    order = (big if len(big) >= MIN_REGIONS_KEPT else ranked[:MIN_REGIONS_KEPT])[:max_regions]

    regions: List[Region] = []
    for rank, i in enumerate(order, start=1):
        area = float(stats[i, cv2.CC_STAT_AREA])
        x, y = int(stats[i, cv2.CC_STAT_LEFT]), int(stats[i, cv2.CC_STAT_TOP])
        bw, bh = int(stats[i, cv2.CC_STAT_WIDTH]), int(stats[i, cv2.CC_STAT_HEIGHT])
        comp = (labels == i).astype(np.uint8)

        cnts, _ = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        poly: List[Tuple[float, float]] = []
        if cnts:
            c = max(cnts, key=cv2.contourArea)
            eps = 0.004 * cv2.arcLength(c, True)
            for p in cv2.approxPolyDP(c, eps, True).reshape(-1, 2)[:80]:
                poly.append((round(float(p[0]) / w, 4), round(float(p[1]) / h, 4)))

        darkness = 0.0
        if rel is not None:
            vals = rel[comp > 0]
            if vals.size:
                darkness = float(np.clip(1.0 - vals.mean(), 0.0, 1.0))

        halo = False
        if uv_mask is not None:
            dil = cv2.dilate(comp, np.ones((15, 15), np.uint8))
            ring = (dil > 0) & (comp == 0)
            if ring.sum() > 0:
                halo = bool((uv_mask[ring] > 0).mean() > 0.35)

        regions.append(Region(
            id=rank,
            bbox=(round(x / w, 4), round(y / h, 4), round(bw / w, 4), round(bh / h, 4)),
            area_pct=round(area * 100.0 / total, 2),
            polygon=poly,
            centroid=(round(float(cents[i][0]) / w, 4), round(float(cents[i][1]) / h, 4)),
            mean_darkness=round(darkness, 3),
            uv_halo=halo,
        ))
    return regions


def analyse(white: np.ndarray, uv: Optional[np.ndarray],
            calib: Optional[Calibration], cfg: dict) -> Tuple[Metrics, np.ndarray, Optional[np.ndarray]]:
    """主入口。返回 (指标, 白光掩膜, UV掩膜)。"""
    wm = segment(white, "white", calib)
    um = segment(uv, "uv", calib) if uv is not None else None

    m = Metrics()
    m.coverage_white_pct = round(_coverage(wm), 2)

    if um is not None:
        m.coverage_uv_pct = round(_coverage(um), 2)
        hs = cfg.get("hidden_spread", {})
        floor = float(hs.get("min_white_pct_for_ratio", 0.5))
        if m.coverage_white_pct >= floor:
            raw = m.coverage_uv_pct / m.coverage_white_pct
            m.hidden_spread_ratio = round(raw * float(hs.get("experience_factor", 1.0)), 2)
        if calib is not None and calib.found:
            m.uv_quantification = "calibrated"
        else:
            # 没有标定卡时渐晕无法归一，UV 数值只能作趋势参考
            m.uv_quantification = "estimated"

    rel_w = _flat_field(white)
    m.regions = extract_regions(wm, um, rel_w)
    if m.regions:
        m.largest_patch_pct = max(r.area_pct for r in m.regions)

    min_pct = float(cfg.get("count_min_blob_area_pct", 0.02))
    n, _, stats, _ = cv2.connectedComponentsWithStats((wm > 0).astype(np.uint8), 8)
    areas = stats[1:, cv2.CC_STAT_AREA] * 100.0 / float(wm.size)
    m.patch_count = int((areas >= min_pct).sum())

    if calib is not None and calib.found:
        px = float((wm > 0).sum())
        m.absolute_area_cm2 = round(calib.area_cm2(px) or 0.0, 1)
        m.scale_source = "calibration_card"

    return m, wm, um


def apply_count_rule(m: Metrics, level: int, cfg: dict) -> None:
    """L3 起菌落融合成片，'数量'失去物理意义 —— 由分级决定该指标是否呈现。"""
    m.count_is_meaningful = level <= int(cfg.get("count_meaningful_max_level", 2))

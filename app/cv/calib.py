"""标定卡检测。

卡片规格：85.6 × 54 mm（银行卡尺寸），四角各一枚 10mm ArUco 标记
(DICT_4X4_50, id 0=左上 1=右上 2=右下 3=左下)，中部横向四色块：
白 / 中性灰 / 荧光参照 / 黑。

它一次解决三件事：
  1. 绝对尺度   —— 覆盖率 % 换算成 cm²
  2. 亮度归一   —— 白块作基准，抵消 UV 手电筒渐晕造成的整体明暗漂移
  3. 白平衡     —— 灰块作基准，抵消不同色温光源

检测不到卡片时全部降级：面积只给百分比，UV 量化标记为估算。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

CARD_W_MM = 85.6
CARD_H_MM = 54.0
_DICT = cv2.aruco.DICT_4X4_50

# 四色块中心在卡面上的相对位置（卡片左上为原点，归一化）
PATCHES: Dict[str, Tuple[float, float]] = {
    # 位置以"四个标记中心构成的矩形"为坐标系（展平后的空间），
    # 与 scripts/make_calibration_card.py 的排版严格对应，改一处必须改两处。
    "white": (0.198, 0.50),
    "grey": (0.399, 0.50),
    "fluor": (0.601, 0.50),
    "black": (0.802, 0.50),
}
PATCH_HALF = 0.055   # 采样窗口半宽（归一化），取中心避免边缘串色


@dataclass
class Calibration:
    found: bool = False
    px_per_mm: float = 0.0
    corners: Optional[List[Tuple[float, float]]] = None   # 归一化四角
    patches: Dict[str, Tuple[float, float, float]] = None  # BGR 均值
    white_luma: float = 0.0
    grey_bgr: Optional[Tuple[float, float, float]] = None

    @property
    def px_per_cm(self) -> float:
        return self.px_per_mm * 10.0

    def area_cm2(self, pixels: float) -> Optional[float]:
        if not self.found or self.px_per_cm <= 0:
            return None
        return float(pixels) / (self.px_per_cm ** 2)


def _detector():
    d = cv2.aruco.getPredefinedDictionary(_DICT)
    try:                                   # OpenCV >= 4.7 新 API
        params = cv2.aruco.DetectorParameters()
        return cv2.aruco.ArucoDetector(d, params)
    except AttributeError:                 # 旧 API 兜底
        return None


def detect(img: np.ndarray) -> Calibration:
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    det = _detector()
    if det is None:
        return Calibration()
    corners, ids, _ = det.detectMarkers(gray)
    if ids is None or len(ids) < 3:
        return Calibration()

    found: Dict[int, np.ndarray] = {}
    for c, i in zip(corners, ids.flatten()):
        if 0 <= int(i) <= 3:
            found[int(i)] = c.reshape(4, 2)
    if len(found) < 3:
        return Calibration()

    # 用标记中心估计卡片四角；缺一角时由另外三角推出
    centers = {k: v.mean(axis=0) for k, v in found.items()}
    if len(centers) == 3:
        missing = ({0, 1, 2, 3} - set(centers)).pop()
        opp = {0: 2, 1: 3, 2: 0, 3: 1}[missing]
        nbrs = [k for k in centers if k != opp]
        centers[missing] = centers[nbrs[0]] + centers[nbrs[1]] - centers[opp]

    quad = np.array([centers[0], centers[1], centers[2], centers[3]], dtype=np.float32)

    top = np.linalg.norm(quad[1] - quad[0])
    bottom = np.linalg.norm(quad[2] - quad[3])
    edge_px = (top + bottom) / 2.0
    # 标记中心间距 = 卡宽 - 一个标记边长（标记居于角内 2mm 边距，10mm 见方）
    span_mm = CARD_W_MM - 2 * (2.0 + 5.0)
    px_per_mm = edge_px / span_mm if span_mm > 0 else 0.0
    if px_per_mm <= 0:
        return Calibration()

    patches = _sample_patches(img, quad)
    white = patches.get("white")
    grey = patches.get("grey")
    return Calibration(
        found=True,
        px_per_mm=float(px_per_mm),
        corners=[(float(p[0] / w), float(p[1] / h)) for p in quad],
        patches=patches,
        white_luma=float(np.mean(white)) if white else 0.0,
        grey_bgr=grey,
    )


def _sample_patches(img: np.ndarray, quad: np.ndarray) -> Dict[str, Tuple[float, float, float]]:
    """把卡面透视展平后取色块均值，避免拍摄角度带来的采样偏移。"""
    side_w, side_h = 358, 200            # 5 px/mm，对应标记中心矩形 71.6 x 40 mm
    dst = np.array([[0, 0], [side_w, 0], [side_w, side_h], [0, side_h]], dtype=np.float32)
    try:
        M = cv2.getPerspectiveTransform(quad, dst)
        flat = cv2.warpPerspective(img, M, (side_w, side_h))
    except cv2.error:
        return {}
    out: Dict[str, Tuple[float, float, float]] = {}
    for name, (fx, fy) in PATCHES.items():
        x0 = int((fx - PATCH_HALF) * side_w); x1 = int((fx + PATCH_HALF) * side_w)
        y0 = int((fy - PATCH_HALF) * side_h); y1 = int((fy + PATCH_HALF) * side_h)
        roi = flat[max(0, y0):y1, max(0, x0):x1]
        if roi.size:
            b, g, r = roi.reshape(-1, 3).mean(axis=0)
            out[name] = (float(b), float(g), float(r))
    return out


def normalize_with_card(img: np.ndarray, calib: Calibration) -> np.ndarray:
    """按灰块做通道增益校正，再按白块把整体亮度拉到标准位。
    只在检测到卡片时调用；否则原样返回。"""
    if not calib.found or not calib.grey_bgr:
        return img
    b, g, r = calib.grey_bgr
    m = (b + g + r) / 3.0
    if m <= 1:
        return img
    gains = np.array([m / max(b, 1.0), m / max(g, 1.0), m / max(r, 1.0)], dtype=np.float32)
    gains = np.clip(gains, 0.5, 2.0)
    out = img.astype(np.float32) * gains.reshape(1, 1, 3)

    if calib.white_luma > 1:
        target = 235.0                     # 白块应落在的亮度
        k = float(np.clip(target / calib.white_luma, 0.6, 1.8))
        out *= k
    return np.clip(out, 0, 255).astype(np.uint8)


def mask_out_card(mask: np.ndarray, calib: Calibration) -> np.ndarray:
    """把卡片本身从霉斑掩膜里挖掉——它是道具，不是墙。"""
    if not calib.found or not calib.corners:
        return mask
    h, w = mask.shape[:2]
    pts = np.array([[int(x * w), int(y * h)] for x, y in calib.corners], dtype=np.int32)
    pad = cv2.convexHull(pts)
    out = mask.copy()
    cv2.fillConvexPoly(out, pad, 0)
    # 标记中心构成的四边形略小于实物卡片，外扩一圈保证挖干净
    cv2.polylines(out, [pad], True, 0, thickness=int(max(6, 0.02 * min(h, w))))
    return out

"""把 UV 图对齐到白光图。

手持拍摄两张必有位移。前端的洋葱皮取景已经把偏差压得很小，这里只是兜底：
ORB 特征匹配 + RANSAC 求单应；特征不足（UV 图对比度低时常见）回退 ECC
灰度配准；再失败就如实报告未配准，差异类指标随之降级，而不是给个错数字。
"""
from __future__ import annotations

from typing import Optional, Tuple

import cv2
import numpy as np

MIN_MATCHES = 12
MAX_SHIFT_RATIO = 0.35        # 位移超过画面 35% 基本可判定不是同一区域


def register(uv: np.ndarray, white: np.ndarray) -> Tuple[np.ndarray, bool, str]:
    """返回 (对齐后的 UV 图, 是否成功, 方法)。失败时返回缩放到同尺寸的原图。"""
    h, w = white.shape[:2]
    uv_r = cv2.resize(uv, (w, h)) if uv.shape[:2] != (h, w) else uv

    out = _orb(uv_r, white)
    if out is not None:
        return out, True, "orb"

    out = _ecc(uv_r, white)
    if out is not None:
        return out, True, "ecc"

    return uv_r, False, "none"


def _prep(img: np.ndarray) -> np.ndarray:
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(g)


def _orb(uv: np.ndarray, white: np.ndarray) -> Optional[np.ndarray]:
    a, b = _prep(uv), _prep(white)
    orb = cv2.ORB_create(nfeatures=3000)
    ka, da = orb.detectAndCompute(a, None)
    kb, db = orb.detectAndCompute(b, None)
    if da is None or db is None or len(ka) < MIN_MATCHES or len(kb) < MIN_MATCHES:
        return None

    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    raw = matcher.knnMatch(da, db, k=2)
    good = [m for m, n in (p for p in raw if len(p) == 2) if m.distance < 0.75 * n.distance]
    if len(good) < MIN_MATCHES:
        return None

    src = np.float32([ka[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([kb[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    H, inliers = cv2.findHomography(src, dst, cv2.RANSAC, 4.0)
    if H is None or inliers is None or int(inliers.sum()) < MIN_MATCHES:
        return None
    if not _plausible(H, white.shape[:2]):
        return None

    h, w = white.shape[:2]
    return cv2.warpPerspective(uv, H, (w, h), borderMode=cv2.BORDER_REPLICATE)


def _plausible(H: np.ndarray, shape: Tuple[int, int]) -> bool:
    """拒绝退化的单应：位移过大、缩放异常、强烈剪切，都说明匹配到了错的东西。"""
    h, w = shape
    tx, ty = float(H[0, 2]), float(H[1, 2])
    if abs(tx) > MAX_SHIFT_RATIO * w or abs(ty) > MAX_SHIFT_RATIO * h:
        return False
    sx = float(np.hypot(H[0, 0], H[1, 0]))
    sy = float(np.hypot(H[0, 1], H[1, 1]))
    if not (0.6 < sx < 1.6 and 0.6 < sy < 1.6):
        return False
    if abs(float(H[2, 0])) > 1e-3 or abs(float(H[2, 1])) > 1e-3:
        return False
    return True


def _ecc(uv: np.ndarray, white: np.ndarray) -> Optional[np.ndarray]:
    a = _prep(uv).astype(np.float32) / 255.0
    b = _prep(white).astype(np.float32) / 255.0
    # 缩小后求解，快且更容易收敛
    scale = 480.0 / max(a.shape)
    if scale < 1.0:
        a_s = cv2.resize(a, None, fx=scale, fy=scale)
        b_s = cv2.resize(b, None, fx=scale, fy=scale)
    else:
        a_s, b_s, scale = a, b, 1.0

    warp = np.eye(2, 3, dtype=np.float32)
    crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 120, 1e-5)
    try:
        _, warp = cv2.findTransformECC(b_s, a_s, warp, cv2.MOTION_EUCLIDEAN, crit, None, 5)
    except cv2.error:
        return None

    warp[0, 2] /= scale
    warp[1, 2] /= scale
    h, w = white.shape[:2]
    if abs(warp[0, 2]) > MAX_SHIFT_RATIO * w or abs(warp[1, 2]) > MAX_SHIFT_RATIO * h:
        return None
    return cv2.warpAffine(uv, warp, (w, h), flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP,
                          borderMode=cv2.BORDER_REPLICATE)

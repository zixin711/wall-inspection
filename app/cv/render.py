"""渲染：掩膜叠加图（同时给用户看和给大模型看）。

给大模型的那张带编号，任务就从"找霉斑"变成"审这些编号区域"——
后者是视觉模型明显更稳的任务，也顺带消除了坐标漂移。
"""
from __future__ import annotations

from typing import List, Optional

import cv2
import numpy as np

from .segment import Region

# BGR。赭石橙：在紫色 UV 底上是互补色，在灰白墙面上也压得住，
# 且与界面配色同族（参考工业设计提案板的暖中性 + 赭石点缀）。
TINT = (69, 138, 224)
OUTLINE = (228, 243, 255)


def overlay(base: np.ndarray, mask: np.ndarray, regions: Optional[List[Region]] = None,
            label: bool = False, alpha: float = 0.38) -> np.ndarray:
    out = base.copy()
    if mask is not None and mask.any():
        tint = np.zeros_like(out)
        tint[:] = TINT
        a = (mask > 0)[..., None].astype(np.float32) * alpha
        out = (out * (1 - a) + tint * a).astype(np.uint8)
        cnts, _ = cv2.findContours((mask > 0).astype(np.uint8),
                                   cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(out, cnts, -1, OUTLINE, max(1, out.shape[1] // 900))

    if regions and label:
        h, w = out.shape[:2]
        scale = max(0.45, w / 1600.0 * 0.9)
        th = max(1, int(w / 900))
        for r in regions:
            x, y, bw, bh = r.bbox
            p1 = (int(x * w), int(y * h))
            p2 = (int((x + bw) * w), int((y + bh) * h))
            cv2.rectangle(out, p1, p2, OUTLINE, th)
            tag = f"#{r.id}"
            (tw, tht), _ = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX, scale, th)
            ty = max(tht + 4, p1[1] - 4)
            cv2.rectangle(out, (p1[0], ty - tht - 4), (p1[0] + tw + 8, ty + 3), (20, 18, 16), -1)
            cv2.putText(out, tag, (p1[0] + 4, ty), cv2.FONT_HERSHEY_SIMPLEX,
                        scale, OUTLINE, th, cv2.LINE_AA)
    return out


def side_by_side(white: np.ndarray, uv: np.ndarray) -> np.ndarray:
    h = min(white.shape[0], uv.shape[0])
    a = cv2.resize(white, (int(white.shape[1] * h / white.shape[0]), h))
    b = cv2.resize(uv, (int(uv.shape[1] * h / uv.shape[0]), h))
    return np.hstack([a, b])


def encode_jpeg(img: np.ndarray, quality: int = 85, max_edge: Optional[int] = None) -> bytes:
    out = img
    if max_edge:
        m = max(out.shape[:2])
        if m > max_edge:
            s = max_edge / m
            out = cv2.resize(out, (int(out.shape[1] * s), int(out.shape[0] * s)),
                             interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("JPEG 编码失败")
    return buf.tobytes()


def encode_mask_png(mask: np.ndarray) -> bytes:
    """带 alpha 的掩膜 PNG，前端可直接叠在原图上做开关显示。"""
    h, w = mask.shape[:2]
    # cv2.imencode 期望 BGRA 顺序，TINT 本身已是 BGR
    rgba = np.zeros((h, w, 4), np.uint8)
    rgba[:, :, 0] = TINT[0]
    rgba[:, :, 1] = TINT[1]
    rgba[:, :, 2] = TINT[2]
    # 赭石橙与 UV 紫是互补色，对比很强。覆盖率高时 150 的不透明度会把
    # 照片本身盖掉，只剩一片橙色；105 足够看清范围，又留得住底下的纹理。
    rgba[:, :, 3] = (mask > 0).astype(np.uint8) * 105
    ok, buf = cv2.imencode(".png", rgba)
    if not ok:
        raise RuntimeError("PNG 编码失败")
    return buf.tobytes()

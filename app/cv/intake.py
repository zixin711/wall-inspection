"""上传预检：不合格的图直接退回可执行的重拍提示，不浪费一次模型调用。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import cv2
import numpy as np

MIN_LONG_EDGE = 640
BLUR_VAR_MIN = 45.0          # Laplacian 方差，低于此判为模糊
OVEREXPOSED_MAX = 0.18       # 过曝像素占比上限
UNDEREXPOSED_MAX = 0.55      # UV 图允许更暗，白光图不应这么暗
UV_AMBIENT_MAX = 0.22        # UV 图中"非紫"高亮像素占比，超过说明没关灯


@dataclass
class QualityIssue:
    code: str
    severity: str            # blocker | warning
    zh: str
    en: str


@dataclass
class QualityReport:
    ok: bool = True
    issues: List[QualityIssue] = field(default_factory=list)
    blur_score: float = 0.0
    overexposed_pct: float = 0.0
    mean_luma: float = 0.0

    def add(self, issue: QualityIssue) -> None:
        self.issues.append(issue)
        if issue.severity == "blocker":
            self.ok = False


def decode(data: bytes) -> Optional[np.ndarray]:
    """解码上传字节。HEIC 由前端在 canvas 上转码为 JPEG，后端只收常规格式。"""
    buf = np.frombuffer(data, np.uint8)
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    return img


def downscale(img: np.ndarray, long_edge: int) -> np.ndarray:
    h, w = img.shape[:2]
    m = max(h, w)
    if m <= long_edge:
        return img
    s = long_edge / m
    return cv2.resize(img, (int(round(w * s)), int(round(h * s))), interpolation=cv2.INTER_AREA)


def _blur_score(gray: np.ndarray) -> float:
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def check(img: np.ndarray, kind: str) -> QualityReport:
    """kind: 'white' | 'uv'"""
    rep = QualityReport()
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    rep.blur_score = _blur_score(gray)
    rep.mean_luma = float(gray.mean())
    rep.overexposed_pct = float((gray >= 250).mean())

    if max(h, w) < MIN_LONG_EDGE:
        rep.add(QualityIssue(
            "too_small", "blocker",
            f"图像分辨率过低（{w}×{h}），请靠近或用更高画质重拍。",
            f"Resolution too low ({w}×{h}). Move closer or raise capture quality.",
        ))

    if rep.blur_score < BLUR_VAR_MIN:
        rep.add(QualityIssue(
            "blurry", "blocker",
            "画面模糊。请稳定手机、待对焦完成后再拍。",
            "Image is out of focus. Hold steady and let autofocus settle.",
        ))

    if rep.overexposed_pct > OVEREXPOSED_MAX:
        rep.add(QualityIssue(
            "overexposed", "warning",
            "画面存在大面积过曝，可能丢失霉斑细节。建议避开直射光或降低曝光。",
            "Large blown-out areas may hide detail. Avoid direct light or lower exposure.",
        ))

    if kind == "white":
        if rep.mean_luma < 55:
            rep.add(QualityIssue(
                "too_dark", "blocker",
                "白光图过暗。请开灯后重拍。",
                "White-light shot is too dark. Turn the lights on and retake.",
            ))
    else:
        rep_amb = _uv_ambient_ratio(img)
        if rep_amb > UV_AMBIENT_MAX:
            rep.add(QualityIssue(
                "ambient_light", "warning",
                "UV 图中环境光偏强，会淹没微弱荧光。请关灯、拉窗帘后重拍。",
                "Ambient light is washing out the fluorescence. Switch lights off and draw curtains.",
            ))
        if rep.mean_luma > 190:
            rep.add(QualityIssue(
                "uv_too_bright", "warning",
                "UV 图整体过亮，可能开着灯或距离过近。",
                "UV frame is very bright — lights may still be on, or the lamp is too close.",
            ))
    return rep


def _uv_ambient_ratio(img: np.ndarray) -> float:
    """UV 场景下墙面应偏紫（Lab-b 显著为负）。既亮又不紫的像素占比高 = 环境光污染。"""
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    L = lab[:, :, 0].astype(np.float32)
    b = lab[:, :, 2].astype(np.float32) - 128.0
    bright_neutral = (L > 150) & (b > -6)
    return float(bright_neutral.mean())


def pair_consistency(white: np.ndarray, uv: np.ndarray) -> Optional[QualityIssue]:
    """两张图长宽比差太多，基本可以断定不是同一面墙/同一构图。"""
    def ar(i: np.ndarray) -> float:
        h, w = i.shape[:2]
        return w / h
    if abs(ar(white) - ar(uv)) > 0.25:
        return QualityIssue(
            "aspect_mismatch", "warning",
            "两张图构图差异较大，可能不是同一区域，差异指标将不可用。",
            "The two frames differ noticeably; they may not cover the same area.",
        )
    return None

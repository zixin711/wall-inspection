#!/usr/bin/env python3
"""生成标定卡印刷稿（300 dpi PNG，85.6 × 54 mm，银行卡尺寸）。

卡面：
  四角 10mm ArUco 标记 (DICT_4X4_50, id 0=左上 1=右上 2=右下 3=左下) —— 定位与尺度
  中部四色块：白 / 中性灰 / 荧光参照 / 黑 —— 亮度归一、白平衡、UV 响应校准

印刷要求（很重要，印错了卡就没用）：
  · 哑光相纸或哑光铜版纸，不要用亮面（会产生镜面高光）
  · 关闭打印机的"鲜艳/增强"色彩处理，用无色彩管理或 sRGB 直出
  · 荧光块请用荧光黄墨或贴一小片荧光标签纸；普通油墨在 365nm 下不发光
  · 打印后量一下实际宽度，若不是 85.6mm 需在 .env 里改 CALIB_CARD_WIDTH_MM

用法：python3 scripts/make_calibration_card.py [输出路径]
"""
import sys
from pathlib import Path

import cv2
import numpy as np

DPI = 300
MM = DPI / 25.4
W_MM, H_MM = 85.6, 54.0
MARKER_MM = 10.0
MARGIN_MM = 2.0

PATCHES = [
    ("WHITE", (255, 255, 255)),
    ("GREY 50", (128, 128, 128)),
    ("FLUOR", (60, 240, 240)),     # 印刷时用荧光墨；此处仅为占位色
    ("BLACK", (16, 16, 16)),
]


def mm(v: float) -> int:
    return int(round(v * MM))


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "calibration_card_85.6x54mm_300dpi.png")
    W, H = mm(W_MM), mm(H_MM)
    card = np.full((H, W, 3), 255, np.uint8)

    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    size = mm(MARKER_MM)
    pos = {
        0: (mm(MARGIN_MM), mm(MARGIN_MM)),
        1: (W - mm(MARGIN_MM) - size, mm(MARGIN_MM)),
        2: (W - mm(MARGIN_MM) - size, H - mm(MARGIN_MM) - size),
        3: (mm(MARGIN_MM), H - mm(MARGIN_MM) - size),
    }
    for mid, (x, y) in pos.items():
        img = cv2.aruco.generateImageMarker(d, mid, size)
        card[y:y + size, x:x + size] = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)

    # 四色块：横向排布在卡片中部，与 app/cv/calib.py 的 PATCHES 位置对应
    strip_x0, strip_x1 = mm(MARGIN_MM * 2 + MARKER_MM), W - mm(MARGIN_MM * 2 + MARKER_MM)
    strip_y0, strip_y1 = mm(18.0), mm(36.0)
    n = len(PATCHES)
    pw = (strip_x1 - strip_x0) // n
    for i, (name, bgr) in enumerate(PATCHES):
        x0 = strip_x0 + i * pw
        cv2.rectangle(card, (x0, strip_y0), (x0 + pw - 2, strip_y1), bgr, -1)
        cv2.rectangle(card, (x0, strip_y0), (x0 + pw - 2, strip_y1), (140, 140, 140), 1)
        cv2.putText(card, name, (x0 + 4, strip_y1 + mm(3.6)), cv2.FONT_HERSHEY_SIMPLEX,
                    0.32, (60, 60, 60), 1, cv2.LINE_AA)

    # 顶部刻度：现场肉眼可核对打印比例是否正确
    ruler_y = mm(14.0)
    for i in range(0, 61, 5):
        x = strip_x0 + mm(i)
        if x > strip_x1:
            break
        h = mm(2.2) if i % 10 == 0 else mm(1.2)
        cv2.line(card, (x, ruler_y), (x, ruler_y - h), (40, 40, 40), 1)
    cv2.putText(card, "0        10       20       30       40       50       60 mm",
                (strip_x0 - 2, ruler_y + mm(3.0)), cv2.FONT_HERSHEY_SIMPLEX,
                0.30, (40, 40, 40), 1, cv2.LINE_AA)

    cv2.putText(card, "MOULD-UV CALIBRATION CARD  85.6 x 54 mm", (strip_x0, mm(8.0)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.36, (30, 30, 30), 1, cv2.LINE_AA)
    cv2.putText(card, "Matte paper - no colour enhancement - verify width after printing",
                (strip_x0, mm(48.0)), cv2.FONT_HERSHEY_SIMPLEX, 0.28, (110, 110, 110), 1, cv2.LINE_AA)
    cv2.rectangle(card, (0, 0), (W - 1, H - 1), (170, 170, 170), 1)

    cv2.imwrite(str(out), card)
    print(f"已生成 {out}  ({W}×{H}px @ {DPI}dpi = {W_MM}×{H_MM}mm)")

    # 自检：生成的卡自己能不能被检出
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.cv.calib import detect
    c = detect(card)
    print(f"自检：检出={c.found}  px/mm={c.px_per_mm:.2f}（应接近 {MM:.2f}）")
    if c.patches:
        print("      色块采样:", {k: tuple(round(x) for x in v) for k, v in c.patches.items()})
    return 0 if c.found else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""回归脚本：在 reference/ 的 10 张样本上跑 CV 管线，打印分级阶梯。

改了 segment.py 的任何参数都应该先跑这个 —— 白光覆盖率必须保持单调递增，
否则阈值判级会失效。
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import cv2
from app.config import get_settings, grading_config
from app.cv import segment as seg
from app.cv.intake import downscale

LEVELS = ["l1", "l2", "l3", "l4", "l5"]


def main() -> int:
    s = get_settings()
    cfg = grading_config()
    ref = s.reference_path
    rows = []
    for lv in LEVELS:
        w = cv2.imread(str(ref / f"pic_{lv}.jpg"))
        u = cv2.imread(str(ref / f"uv_{lv}.jpg"))
        if w is None or u is None:
            print(f"缺少参考图: {lv}", file=sys.stderr)
            return 2
        w = downscale(w, s.analysis_long_edge)
        u = downscale(u, s.analysis_long_edge)
        u = cv2.resize(u, (w.shape[1], w.shape[0]))
        m, _, _ = seg.analyse(w, u, None, cfg)
        rows.append((lv.upper(), m))

    hdr = f"{'级别':<6}{'白光%':>9}{'UV%':>9}{'倍数':>8}{'最大连片%':>11}{'斑块数':>8}"
    print(hdr); print("-" * 52)
    for name, m in rows:
        r = f"{m.hidden_spread_ratio:.1f}x" if m.hidden_spread_ratio else "  - "
        print(f"{name:<6}{m.coverage_white_pct:>9.2f}{(m.coverage_uv_pct or 0):>9.2f}"
              f"{r:>8}{m.largest_patch_pct:>11.2f}{m.patch_count:>8}")

    whites = [m.coverage_white_pct for _, m in rows]
    mono = all(whites[i] < whites[i + 1] for i in range(len(whites) - 1))
    print()
    print("白光覆盖率单调递增:", "通过 ✓" if mono else "不通过 ✗  <-- 阈值判级会失效")

    # 用配置阈值反查分级，检验 grading.yaml 与实测是否自洽
    print("\n阈值判级自检（CV 兜底路径）:")
    ok = True
    for i, (name, m) in enumerate(rows, start=1):
        from app.arbitrate import grade_from_metrics
        g = grade_from_metrics(m, cfg)
        flag = "✓" if g == i else f"✗ 期望 L{i}"
        if g != i:
            ok = False
        print(f"  {name} -> CV 判定 L{g}  {flag}")
    return 0 if (mono and ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())

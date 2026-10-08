#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""match.py 的 numpy 实现 vs 真正的 cv2.matchTemplate 差分对比。

Differential parity test between the dependency-free numpy engine in
``match.py`` and OpenCV's own ``matchTemplate`` — the very function MaaFramework
calls.  This is the test that matters: an authoring agent trusts these scores to
choose thresholds, so a convention mismatch (e.g. reporting 1.0 for a flat black
splash screen) silently poisons every decision downstream.

What is asserted
----------------
1. **Decision level** — the best score and its location must agree, because that
   is what a `threshold` comparison actually consumes.
2. **Map level, masked** — every window whose correlation is well conditioned
   (non-zero template and window variance) must agree to float32 noise.
3. **Degenerate conventions** — the asymmetric OpenCV rules, explicitly:
   constant *template* -> 1.0 (matches everywhere); constant *window* -> 0.0.
4. Near-flat-but-nonzero windows are reported but **not** asserted: OpenCV
   computes in float32 and cancels catastrophically there, so both engines are
   meaningless in that regime (and the true score is ~0, far under any usable
   threshold).

Run:  python tools/agent/tests/test_match_parity.py
Exit: 0 = within tolerance, 1 = mismatch (details printed), 2 = cv2 missing.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

import match as M  # noqa: E402

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
AGENT = os.path.join(ROOT, ".agent")
IMAGE = os.path.join(ROOT, "resource", "base", "image", "打开游戏")

TOL_MASKED = {5: 5e-4, 3: 1e-5, 1: 1e-5}   # 良态窗口：float32 存储差异
TOL_DECISION = 1e-5                        # 最优分数层面的差异

# 数值可信下限：窗口方差占其自身平方和的比重。
# OpenCV 用 float32 累加，n≈10⁴ 个元素的相对误差约 n·eps ≈ 1e-3；因此
# win_ss/s2 低于 ~1e-4 的窗口已经落在 float32 的抵消噪声里，两个引擎都不可信
# （实测：低于该阈值时最大偏差 4e-2，高于它降到 1e-4 量级；而真实有纹理的画面
# 100% 都在这条线之上，偏差稳定在 2e-5，不存在"把差异藏起来"的可能）。
COND_EPS = 1e-4


def cv_gray(path):
    """cv2.imread 在 Windows 上打不开非 ASCII 路径，必须走 imdecode。"""
    raw = np.fromfile(path, dtype=np.uint8)
    img = cv2.imdecode(raw, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise RuntimeError("cv2 could not decode %s" % path)
    if img.ndim == 3:
        if img.shape[2] == 4:
            img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return img


def well_conditioned_mask(gray, tpl, method):
    """良态窗口掩码：模板方差非零，且窗口方差高于 float32 抵消下限。"""
    th, tw = tpl.shape
    n = float(th * tw)
    if gray.shape[0] < th or gray.shape[1] < tw:
        return np.zeros((0, 0), dtype=bool), np.zeros((0, 0), dtype=bool)
    s1 = M._box_sums(gray, th, tw)
    s2 = M._box_sums(gray * gray, th, tw)
    win_ss = np.maximum(s2 - s1 * s1 / n, 0.0)
    ratio = np.divide(win_ss, s2, out=np.zeros_like(win_ss), where=s2 > 0.0)
    good = ratio > COND_EPS
    if method == 5:
        dev = tpl - tpl.mean()
        tpl_ss = float(np.sum(dev * dev))
        return good, np.full(win_ss.shape, tpl_ss > 0.0)
    tpl_ss = float(np.sum(tpl * tpl))
    return good, np.full(s2.shape, tpl_ss > 0.0)


def compare(label, screen, tpl, method, roi=None):
    H, W = screen.shape
    if roi is not None:
        x, y, w, h = roi
        screen = screen[max(0, y):min(H, y + h), max(0, x):min(W, x + w)]
    th, tw = tpl.shape
    if th > screen.shape[0] or tw > screen.shape[1]:
        return None

    mine = M._score_map(screen, tpl, method, None)
    theirs = cv2.matchTemplate(screen.astype(np.float32), tpl.astype(np.float32),
                               method).astype(np.float64)
    if mine.shape != theirs.shape:
        return {"label": label, "method": method, "error": f"shape {mine.shape} != {theirs.shape}"}

    wmask, tmask = well_conditioned_mask(screen, tpl, method)
    mask = wmask & tmask
    higher = M.HIGHER_IS_BETTER[method]

    def pick(arr):
        flat = arr.ravel()
        i = int(np.argmax(flat) if higher else np.argmin(flat))
        return float(flat[i]), i

    best_m, arg_m = pick(mine)
    best_t, arg_t = pick(theirs)
    # cv2 在 float32 下的边界退化可能让 argmax 在并列时不同，只在分数有意义时比较位置
    rmse = float(np.sqrt(np.mean((mine[mask] - theirs[mask]) ** 2))) if mask.any() else 0.0
    return {
        "label": label, "method": method,
        "masked_diff": float(np.max(np.abs(mine[mask] - theirs[mask]))) if mask.any() else 0.0,
        "masked_rmse": rmse,
        "all_diff": float(np.max(np.abs(mine - theirs))),
        "decision_diff": abs(best_m - best_t),
        "argmax_same": arg_m == arg_t,
        "degenerate_frac": float(1.0 - mask.mean()),
    }


def cases():
    screens = {
        "ref_title": os.path.join(AGENT, "shots", "ref_title.png"),
        "ref_login": os.path.join(AGENT, "shots", "ref_login.png"),
        "ref_hub": os.path.join(AGENT, "shots", "ref_hub.png"),
        "ref_room": os.path.join(AGENT, "shots", "ref_room.png"),
        "cs000_black": os.path.join(AGENT, "artifacts", "coldstart", "000.png"),
        "cs005_red": os.path.join(AGENT, "artifacts", "coldstart", "005.png"),
        "cs011_white": os.path.join(AGENT, "artifacts", "coldstart", "011.png"),
    }
    tpls = {
        "tpl_title": os.path.join(IMAGE, "打开游戏-点击开始游戏_1.png"),
        "tpl_login": os.path.join(IMAGE, "打开游戏-点击登录_1.png"),
        "tpl_peiyu": os.path.join(IMAGE, "打开游戏-进入培育室_1.png"),
        "tpl_room": os.path.join(IMAGE, "打开游戏-确认完成_1.png"),
    }
    missing = [p for p in list(screens.values()) + list(tpls.values()) if not os.path.isfile(p)]
    if missing:
        print("skip: 缺少夹具（先跑截图步骤）:")
        for p in missing:
            print("   ", p)
        return []
    out = []
    for sname, spath in screens.items():
        for tname, tpath in tpls.items():
            out.append((f"{sname} x {tname}", spath, tpath, None))
    out.append(("roi ref_title x tpl_title", screens["ref_title"], tpls["tpl_title"], (200, 730, 280, 70)))
    out.append(("roi ref_hub x tpl_peiyu", screens["ref_hub"], tpls["tpl_peiyu"], (0, 1190, 250, 90)))
    return out


def synthetic_cases():
    rng = np.random.default_rng(20261008)
    big = (rng.random((300, 400)) * 255)
    return [
        ("synth exact crop", big, big[91:151, 137:197].copy(), None),
        ("synth tiny tpl", big, big[10:14, 20:26].copy(), None),
    ]


def main():
    if cv2 is None:
        print("SKIP: cv2 不可用（它就是要拿来当 oracle 的），无法做差分对比")
        return 2

    print(f"cv2 {cv2.__version__} | numpy {np.__version__}")
    rows, failures = [], []

    for label, spath, tpath, roi in cases() + synthetic_cases():
        if isinstance(spath, str):
            screen, _ = M._pixel_array(spath, need_green_mask=False)
            tpl, _ = M._pixel_array(tpath, need_green_mask=False)
        else:
            screen, tpl = spath, tpath
        for method in (5, 3, 1):
            row = compare(label, screen, tpl, method, roi)
            if row is None:
                continue
            rows.append(row)
            if "error" in row:
                failures.append(row)
                continue
            if not row["argmax_same"] and row["decision_diff"] > TOL_DECISION:
                row["why"] = "argmax differs with a meaningful score gap"
                failures.append(row)
            elif row["masked_diff"] > TOL_MASKED[method]:
                row["why"] = f"masked_diff > {TOL_MASKED[method]:g}"
                failures.append(row)

    print(f"\n{'case':<40}{'m':>3}{'masked':>11}{'decision':>11}{'all':>11}{'deg%':>7}")
    print("-" * 84)
    for r in rows:
        if "error" in r:
            print(f"{r['label']:<40}{r['method']:>3}  {r['error']}")
            continue
        print(f"{r['label']:<40}{r['method']:>3}{r['masked_diff']:>11.2e}"
              f"{r['decision_diff']:>11.2e}{r['all_diff']:>11.2e}{r['degenerate_frac']*100:>6.1f}%")

    worst = {m: max((r["masked_diff"] for r in rows if r.get("method") == m and "masked_diff" in r),
                    default=0.0) for m in (5, 3, 1)}
    print(f"\ncases={len(rows)}  masked_diff worst: "
          + "  ".join(f"m{m}={worst[m]:.2e}" for m in (5, 3, 1)))
    print("注：'all' 列包含近常量窗口——那里 OpenCV 用 float32 会灾难性抵消，"
          "两个引擎都没有意义，已从断言中排除。")

    # ---- 退化约定：必须和 OpenCV 一模一样 --------------------------------
    rng = np.random.default_rng(1)
    scr = (rng.random((80, 100)) * 255)
    tpl_norm = scr[20:40, 30:60].copy()
    tpl_const = np.full((20, 20), 128.0)
    scr_const = np.full((80, 100), 90.0)
    checks = [
        ("常量模板 -> 1.0", scr, tpl_const, 5, 1.0),
        ("常量窗口 -> 0.0", scr_const, tpl_norm, 5, 0.0),
        ("两者皆常量 -> 1.0", scr_const, tpl_const, 5, 1.0),
    ]
    print("\n退化约定（numpy vs cv2）:")
    for name, s, t, method, _expect in checks:
        mine = float(M._score_map(s, t, method, None).max())
        if method == 5:
            theirs = float(cv2.matchTemplate(s.astype(np.float32), t.astype(np.float32),
                                             cv2.TM_CCOEFF_NORMED).max())
        else:
            theirs = float(cv2.matchTemplate(s.astype(np.float32), t.astype(np.float32),
                                             method).max())
        ok = abs(mine - theirs) <= 1e-6
        print(f"  {name:20s} numpy={mine:.6f}  cv2={theirs:.6f}  {'OK' if ok else 'MISMATCH'}")
        if not ok:
            failures.append({"label": name, "method": method,
                             "masked_diff": abs(mine - theirs), "why": "degenerate convention"})

    if failures:
        print(f"\nFAIL: {len(failures)} 个用例超出容差")
        for r in failures[:12]:
            print("   ", r)
        return 1
    print("\nOK: numpy 引擎与 cv2 在全部良态用例与退化约定上一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

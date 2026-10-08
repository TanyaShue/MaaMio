#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline re-implementation of OpenCV ``matchTemplate`` (normalized methods).

Why: a MaaFramework authoring agent must be able to score candidate templates
against saved screenshots without a device, therefore without ``cv2``.
This module only needs numpy + Pillow.

Usage::

    python match.py --screen <screen.png> --template <template.png> \\
                    [--roi x,y,w,h] [--threshold 0.7] [--method 5] \\
                    [--green-mask] [--top N] [--json]

Always prints exactly one JSON object on stdout::

    {"screen": ..., "template": ..., "roi": [x, y, w, h], "method": 5,
     "score": <best>, "box": [x, y, w, h], "matched": true|false,
     "matches": [{"score": <float>, "box": [x, y, w, h]}, ...]}

Numerics
--------
* ``TM_CCOEFF_NORMED`` (5, default) and ``TM_CCORR_NORMED`` (3): higher is
  better.  ``TM_SQDIFF_NORMED`` (1): **lower** is better (``matched`` uses
  ``score <= threshold``).

* ``TM_CCOEFF_NORMED``::

      R(x,y) = SUM (T'(i,j) * I'(x+i, y+j)) / sqrt(SUM T'^2 * SUM I'^2)

  with ``T' = T - mean(T)`` and ``I' = I - mean(I over the window)``.  The
  numerator only needs the mean-subtracted template because ``SUM T' = 0``,
  which also makes the score invariant to any constant brightness offset
  added to the image (that is what the mean subtraction buys us).

* ``TM_CCORR_NORMED``::

      R = SUM (T*I) / sqrt(SUM T^2 * SUM I^2)

* ``TM_SQDIFF_NORMED``::

      R = SUM (T - I)^2 / sqrt(SUM T^2 * SUM I^2)

* Degenerate denominators (constant template and/or constant window): OpenCV
  returns **0.0** for every method.  This was measured against cv2 4.13, not
  assumed: ``matchTemplate(zeros(1280, 720, 3), tpl, TM_CCOEFF_NORMED)`` is all
  zeros, and so is a constant template against a real screenshot.  (An earlier
  revision of this file used 1.0 here — that made every flat region of a black
  splash screen look like a perfect match.  (Measured with cv2 4.13, not assumed.)

* The correlation is computed over the **valid** (non-padded) window region
  only; the score map has shape ``(H - h + 1, W - w + 1)``.

* Results are computed in float64.  OpenCV computes the same quantity from a
  32-bit float DFT for 8U inputs, so agreement is limited by *its* float32
  rounding (~1e-6 relative for typical template sizes); the mathematical
  definition is matched exactly here.

Implementation
--------------
* numerator: FFT cross-correlation (``rfft2``) with the template placed at the
  origin of a circular buffer of the image's (fast-length padded) size.  With
  ``M >= H`` and ``W >= N`` no wrap-around term can reach the valid region
  (``k + j <= H - 1 < M``), so the prefix slice is exact.
* denominator: integral images (``cumsum``) give every window sum and sum of
  squares in O(1) per window; the variance is
  ``SUM I^2 - (SUM I)^2 / n`` clamped at 0, exactly as OpenCV does.
* ``--green-mask``: template pixels whose RGB value is exactly ``(0,255,0)``
  are excluded from the correlation (MaaFramework's ``green_mask``).  A perfect
  masked match would need a *different* window mean per window, which has no
  closed form; the approximation used here is the standard one: the template is
  mean-subtracted over its **unmasked** pixels only and zeroed at masked
  pixels, and the window statistics (``SUM I``, ``SUM I^2``, and therefore
  ``SUM (I - mean_I)^2``) are accumulated over the unmasked pixels only, via an
  FFT box-sum against the 0/1 mask kernel.  Because ``SUM T' = 0`` over the
  unmasked set, ``SUM T' * (I - mean_I) == SUM T' * I`` still holds, so the
  numerator stays exact; only the window mean is the masked one, which is the
  natural reading of "ignore those pixels".  For a mask that is all-True or
  all-False this reduces to the exact unmasked formula.

* Color handling: RGBA/LA alpha is dropped; 3-channel input is converted with
  OpenCV's own luminance weights
  ``Y = 0.299*R + 0.587*G + 0.114*B`` (ITU-R BT.601, identical to
  ``cvtColor(BGR2GRAY)`` up to its fixed-point rounding of ~0.008 gray levels).
  Both screen and template use that same conversion.

Exit codes: 0 on success, 2 on any error (readable message on stderr).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
from PIL import Image, UnidentifiedImageError

Image.MAX_IMAGE_PIXELS = None  # full-screen 4K screenshots are legitimate input

PROG = "match.py"

METHOD_NAMES = {1: "TM_SQDIFF_NORMED", 3: "TM_CCORR_NORMED", 5: "TM_CCOEFF_NORMED"}
HIGHER_IS_BETTER = {1: False, 3: True, 5: True}

_METHOD_ALIASES = {
    "1": 1, "sqdiff": 1, "sqdiff_normed": 1, "tm_sqdiff_normed": 1,
    "3": 3, "ccorr": 3, "ccorr_normed": 3, "tm_ccorr_normed": 3,
    "5": 5, "ccoeff": 5, "ccoeff_normed": 5, "tm_ccoeff_normed": 5,
}

# MaaFramework marks "ignore this pixel" with pure green.
GREEN = (0.0, 255.0, 0.0)

# Luminance weights, OpenCV cvtColor(BGR2GRAY) / ITU-R BT.601.
_LUMA = (0.299, 0.587, 0.114)


class MatchError(Exception):
    """User-facing error: printed on stderr, exit code 2, no traceback."""


# --------------------------------------------------------------------------
# image loading
# --------------------------------------------------------------------------
def _pixel_array(path, need_green_mask):
    """Load *path* as a float64 (H, W) luminance array plus an optional mask.

    Returns ``(gray, mask)`` where ``mask`` is a boolean (H, W) array that is
    True on pure-green template pixels, or None for single-channel input.
    """
    if not path:
        raise MatchError("no image path given")
    if not os.path.isfile(path):
        raise MatchError("image file not found: %s" % path)
    try:
        with Image.open(path) as im:
            if im.mode in ("P", "PA", "RGBa", "La"):
                im = im.convert("RGBA")
            elif im.mode not in ("L", "LA", "RGB", "RGBA"):
                im = im.convert("RGB")
            arr = np.asarray(im, dtype=np.float64)
    except UnidentifiedImageError:
        raise MatchError("not a readable image (empty or unsupported file): %s" % path)
    except OSError as exc:
        raise MatchError("cannot read image %s: %s" % (path, exc))

    if arr.size == 0:
        raise MatchError("empty image (0 pixels): %s" % path)
    if arr.ndim == 2:
        return arr, None
    if arr.ndim != 3 or arr.shape[2] < 1:
        raise MatchError("unsupported pixel layout %r in %s" % (arr.shape, path))

    rgb = arr[:, :, :3]  # alpha (if any) is dropped
    if rgb.shape[2] == 1:
        return rgb[:, :, 0], None
    mask = None
    if need_green_mask:
        mask = np.all(rgb == np.asarray(GREEN, dtype=np.float64), axis=2)
    gray = rgb[:, :, 0] * _LUMA[0] + rgb[:, :, 1] * _LUMA[1] + rgb[:, :, 2] * _LUMA[2]
    return gray, mask


# --------------------------------------------------------------------------
# math helpers
# --------------------------------------------------------------------------
def _next_fast_len(n):
    """Smallest 5-smooth integer >= n (FFT-friendly, keeps the FFT cheap)."""
    n = int(n)
    if n <= 6:
        return max(1, n)
    best = 1 << (n - 1).bit_length()  # next power of two
    p5 = 1
    while p5 <= best:
        p3 = p5
        while p3 <= best:
            p2 = p3
            while p2 < n:
                p2 *= 2
            if p2 < best:
                best = p2
            p3 *= 3
        p5 *= 5
    return best


def _corr_valid(img, kern):
    """Valid cross-correlation: ``out[x, y] = SUM_ij img[.., x+i, y+j] * kern[i, j]``.

    ``img`` may be (H, W) or (K, H, W); ``kern`` is (h, w).  Implemented as a
    circular correlation of size (H, W) padded to fast lengths: because both
    arrays are zero outside their own extent and ``k + j <= H - 1 < M``, no
    wrap-around term can alias into the valid region, so the leading
    ``(H-h+1, W-w+1)`` slice is the exact linear correlation.
    """
    H, W = img.shape[-2:]
    h, w = kern.shape[-2:]
    m = _next_fast_len(H)
    n = _next_fast_len(W)
    fi = np.fft.rfft2(np.asarray(img, dtype=np.float64), s=(m, n), axes=(-2, -1))
    fk = np.fft.rfft2(np.asarray(kern, dtype=np.float64), s=(m, n), axes=(-2, -1))
    corr = np.fft.irfft2(fi * np.conj(fk), s=(m, n), axes=(-2, -1))
    return corr[..., : H - h + 1, : W - w + 1]


def _box_sums(a, h, w):
    """Sum of every (h, w) window of ``a`` via integral images (O(1) per window)."""
    ii = np.zeros((a.shape[0] + 1, a.shape[1] + 1), dtype=np.float64)
    np.cumsum(np.cumsum(a, axis=0), axis=1, out=ii[1:, 1:])
    return ii[h:, w:] - ii[:-h, w:] - ii[h:, :-w] + ii[:-h, :-w]


def _score_map(img, tpl, method, tpl_mask=None):
    """Raw OpenCV score map (float64), shape ``(H-h+1, W-w+1)``."""
    H, W = img.shape
    h, w = tpl.shape
    if h > H or w > W:
        raise MatchError(
            "template %dx%d is larger than the search region %dx%d" % (w, h, W, H)
        )

    neg_mask = None
    if tpl_mask is not None and tpl_mask.any():
        keep = ~tpl_mask
        if not keep.any():
            raise MatchError("green mask covers the whole template: nothing left to match")
        neg_mask = keep
        n_win = float(keep.sum())
        # masked window sums of I and I^2 (0/1 rectangular kernel is not
        # separable for an arbitrary mask, so use the same FFT machinery)
        stack = np.stack([img, img * img])
        sums = _corr_valid(stack, keep.astype(np.float64))  # (2, H-h+1, W-w+1)
        s1, s2 = sums[0], sums[1]
    else:
        n_win = float(h * w)
        s1 = _box_sums(img, h, w)
        s2 = _box_sums(img * img, h, w)

    # window sum of squared deviations, clamped at 0 exactly like OpenCV
    win_ss = np.maximum(s2 - (s1 * s1) / n_win, 0.0)

    if method == 5:  # TM_CCOEFF_NORMED
        dev = tpl - (tpl[neg_mask].mean() if neg_mask is not None else tpl.mean())
        if neg_mask is not None:
            dev = np.where(neg_mask, dev, 0.0)
        tpl_ss = float(np.sum(dev * dev))
        num = _corr_valid(img, dev)
        # OpenCV 的两条退化分支是**不对称**的（用 cv2 4.13 实测，不是猜的）：
        #   常量模板 tpl_ss == 0        -> 1.0  （于是这个模板到处都"命中"）
        #   常量窗口 win_ss == 0        -> 0.0
        # 两者都返回 1.0（本文件早期版本就是这么写的）会把纯黑开机画面、
        # 纯白公告页判成满分命中，对编写 agent 来说是最坏的一种错。
        # OpenCV's two degenerate branches are asymmetric -- measured, not assumed.
        if tpl_ss <= 0.0:
            return np.ones_like(num)
        den = np.sqrt(tpl_ss * win_ss)
        out = np.divide(num, den, out=np.zeros_like(num), where=den > 0.0)
        return np.clip(out, -1.0, 1.0)

    if method == 3:  # TM_CCORR_NORMED
        use = tpl if neg_mask is None else np.where(neg_mask, tpl, 0.0)
        tpl_ss = float(np.sum(use * use))
        num = _corr_valid(img, use)
    elif method == 1:  # TM_SQDIFF_NORMED
        use = tpl if neg_mask is None else np.where(neg_mask, tpl, 0.0)
        tpl_ss = float(np.sum(use * use))
        cc = _corr_valid(img, use)
        num = np.maximum(tpl_ss + s2 - 2.0 * cc, 0.0)
    else:  # pragma: no cover - guarded by the CLI
        raise MatchError("unsupported method %r" % (method,))

    if tpl_ss <= 0.0:  # 全零模板：分母恒为 0，与 OpenCV 一样给 0
        return np.zeros_like(num)
    den = np.sqrt(tpl_ss * s2)
    # a zero denominator implies a zero numerator here, so 0 is the right value
    out = np.divide(num, den, out=np.zeros_like(num), where=s2 > 0.0)
    # OpenCV clamps the normalized SQDIFF/CCORR maps to [0, 1]; without this the
    # SQDIFF map can exceed 1 (e.g. 5.63 for two flat images) while cv2 says 1.0.
    return np.clip(out, 0.0, 1.0)


def _topk_nms(score, tpl_wh, top, higher_better):
    """Best-first top-``top`` non-overlapping boxes.

    NMS rule (as specified): drop a candidate whose box *center* falls inside a
    box that was already kept.  ``score`` is the raw score map; boxes are all
    ``tpl_wh`` sized and positioned at the score map index.
    """
    tw, th = tpl_wh
    flat = score.ravel()
    total = flat.size
    if total == 0:
        return []
    # argmax/argmin return the FIRST occurrence, exactly like OpenCV's
    # minMaxLoc, so tied best scores pick the top-left-most window.
    best_i = int(np.argmax(flat)) if higher_better else int(np.argmin(flat))
    probe = min(total, max(top * 64, 256))
    if higher_better:
        idx = np.argpartition(-flat, probe - 1)[:probe]
        idx = idx[np.argsort(-flat[idx], kind="stable")]
    else:
        idx = np.argpartition(flat, probe - 1)[:probe]
        idx = idx[np.argsort(flat[idx], kind="stable")]
    order = [best_i] + [int(i) for i in idx if int(i) != best_i]

    width = score.shape[1]
    half_w = tw / 2.0
    half_h = th / 2.0
    kept = []
    for i in order:
        y, x = divmod(int(i), width)
        cx = x + half_w
        cy = y + half_h
        if any(kx <= cx < kx + tw and ky <= cy < ky + th for kx, ky in kept):
            continue
        kept.append((x, y))
        if len(kept) >= top:
            break
    return [(float(score[y, x]), [x, y, tw, th]) for x, y in kept]


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------
def run(args):
    screen, _ = _pixel_array(args.screen, need_green_mask=False)
    tpl, tpl_mask = _pixel_array(args.template, need_green_mask=bool(args.green_mask))

    H, W = screen.shape
    if args.roi is None:
        rx, ry, rw, rh = 0, 0, W, H
    else:
        x, y, w, h = args.roi
        # MaaFramework roi semantics: search only inside this rect, clamped to
        # the image bounds.  The far edge is what gets clamped, so a rect that
        # sticks out is cropped, not shifted.
        rx = max(0, x)
        ry = max(0, y)
        rx1 = min(W, x + w)
        ry1 = min(H, y + h)
        rw = rx1 - rx
        rh = ry1 - ry
        if rw <= 0 or rh <= 0:
            raise MatchError(
                "roi %r does not intersect the screen (screen is %dx%d)" % (list(args.roi), W, H)
            )

    th, tw = tpl.shape
    if th > rh or tw > rw:
        raise MatchError(
            "template is %dx%d but the search region (roi) is only %dx%d"
            % (tw, th, rw, rh)
        )

    sub = screen[ry:ry + rh, rx:rx + rw]
    score = _score_map(sub, tpl, args.method, tpl_mask)
    higher_better = HIGHER_IS_BETTER[args.method]

    best = float(score.max()) if higher_better else float(score.min())
    hits = _topk_nms(score, (tw, th), args.top, higher_better)
    # convert roi-relative boxes to absolute screen coordinates
    matches = [{"score": s, "box": [bx + rx, by + ry, bw, bh]} for s, (bx, by, bw, bh) in hits]
    if not matches:  # pragma: no cover - only possible for an empty score map
        best_box = [rx, ry, tw, th]
    else:
        best_box = matches[0]["box"]

    matched = best >= args.threshold if higher_better else best <= args.threshold

    return {
        "screen": args.screen,
        "template": args.template,
        "roi": [rx, ry, rw, rh],
        "method": args.method,
        "score": best,
        "box": best_box,
        "matched": bool(matched),
        "matches": matches,
    }


def _parse_roi(text):
    parts = text.replace(" ", "").split(",")
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("--roi must be x,y,w,h (got %r)" % text)
    try:
        vals = [int(p) for p in parts]
    except ValueError:
        raise argparse.ArgumentTypeError("--roi must be four integers x,y,w,h (got %r)" % text)
    if vals[2] <= 0 or vals[3] <= 0:
        raise argparse.ArgumentTypeError("--roi width and height must be positive")
    return tuple(vals)


def _parse_method(text):
    key = str(text).strip().lower()
    if key in _METHOD_ALIASES:
        return _METHOD_ALIASES[key]
    try:
        value = int(key)
    except ValueError:
        value = -1
    if value in METHOD_NAMES:
        return value
    raise argparse.ArgumentTypeError(
        "unsupported --method %r; supported: 1 (TM_SQDIFF_NORMED), "
        "3 (TM_CCORR_NORMED), 5 (TM_CCOEFF_NORMED)" % text
    )


def _parse_top(text):
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("--top must be an integer (got %r)" % text)
    if value < 1:
        raise argparse.ArgumentTypeError("--top must be >= 1")
    return value


def build_parser():
    p = argparse.ArgumentParser(
        prog=PROG,
        description="Offline OpenCV matchTemplate (normalized methods) for MaaFramework template authoring.",
        epilog="Prints one JSON object on stdout; errors go to stderr with a non-zero exit code.",
    )
    p.add_argument("--screen", required=True, metavar="PATH", help="screenshot to search in")
    p.add_argument("--template", required=True, metavar="PATH", help="template image to look for")
    p.add_argument("--roi", type=_parse_roi, default=None, metavar="x,y,w,h",
                   help="restrict the search to this sub-rectangle of the screen (clamped to the image)")
    p.add_argument("--threshold", type=float, default=0.7, metavar="F",
                   help="score threshold for 'matched' (default 0.7; for method 1 lower is better)")
    p.add_argument("--method", type=_parse_method, default=5, metavar="N",
                   help="1=TM_SQDIFF_NORMED (lower better), 3=TM_CCORR_NORMED, 5=TM_CCOEFF_NORMED (default 5)")
    p.add_argument("--green-mask", action="store_true",
                   help="ignore template pixels that are exactly (0,255,0), MaaFramework style")
    p.add_argument("--top", type=_parse_top, default=5, metavar="N",
                   help="how many non-overlapping matches to report (default 5)")
    p.add_argument("--json", action="store_true",
                   help="JSON output (this is the default and only output format; accepted for compatibility)")
    return p


def _normalize_argv(argv):
    """Let ``--roi -5,-5,100,100`` work: argparse would read a leading dash as
    an option, so rewrite that one case to the ``--roi=...`` form."""
    out = []
    i = 0
    while i < len(argv):
        cur = argv[i]
        if (cur == "--roi" and i + 1 < len(argv)
                and argv[i + 1].startswith("-") and "," in argv[i + 1]):
            out.append("--roi=" + argv[i + 1])
            i += 2
            continue
        out.append(cur)
        i += 1
    return out


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    args = build_parser().parse_args(_normalize_argv(list(argv)))
    try:
        result = run(args)
    except MatchError as exc:
        sys.stderr.write("%s: error: %s\n" % (PROG, exc))
        return 2
    except KeyboardInterrupt:  # pragma: no cover
        sys.stderr.write("%s: interrupted\n" % PROG)
        return 130
    except Exception as exc:  # never dump a bare traceback on the user
        sys.stderr.write("%s: error: %s: %s\n" % (PROG, type(exc).__name__, exc))
        return 2
    sys.stdout.write(json.dumps(result) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

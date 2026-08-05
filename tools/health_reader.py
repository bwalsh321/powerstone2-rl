"""
health_reader.py — reads the four Power Stone 2 health bars from a captured frame.

The health fill in PS2 is pure bright yellow (BGR ~ (0,255,255)); the empty part
of the bar shows the dark track/border color instead. So "how full is the bar"
is just: what fraction of the bar's columns still contain yellow pixels.

Bar positions are stored as FRACTIONS of the frame size, so this works at any
capture resolution as long as the capture region covers the same view of the
game each time.

Calibration
-----------
The DEFAULT_BARS below were measured from Blake's screenshot (1096x615, Flycast
with the stats bar visible at the top). If your capture region is different
(e.g. game viewport only, different emulator, different window size), grab ONE
screenshot at the start of a match while everyone is at full health and run:

    python health_reader.py my_screenshot.png --calibrate

It will auto-detect the bars, print the new fractions, and save them to
health_bars.json (which is loaded automatically if present).

To just test a reading:

    python health_reader.py my_screenshot.png
"""

import json
import os

import cv2
import numpy as np

CALIB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "health_bars.json")

# (x0, x1, y0, y1) as fractions of (width, width, height, height).
# Measured from the 2026-07-30 screenshot: bar band rows 537-552 of 615.
DEFAULT_BARS = {
    "y0": 0.8732, "y1": 0.8976,
    "players": [
        {"name": "1P", "x0": 0.1086, "x1": 0.2144},
        {"name": "2P", "x0": 0.3568, "x1": 0.4662},
        {"name": "3P", "x0": 0.6077, "x1": 0.7135},
        {"name": "4P", "x0": 0.8568, "x1": 0.9626},
    ],
}


def _yellow_mask(img):
    """Boolean mask of 'health yellow' pixels (BGR frame)."""
    b = img[:, :, 0].astype(np.int32)
    g = img[:, :, 1].astype(np.int32)
    r = img[:, :, 2].astype(np.int32)
    return (r > 200) & (g > 200) & (b < 100)


def load_calibration():
    if os.path.exists(CALIB_FILE):
        with open(CALIB_FILE) as f:
            return json.load(f)
    return DEFAULT_BARS


def read_health(frame, bars=None):
    """
    frame: full BGR capture of the game (numpy array, any resolution).
    Returns [h1, h2, h3, h4] with each h in [0.0, 1.0].

    A KO'd / absent player's bar reads ~0.0.
    """
    if bars is None:
        bars = load_calibration()
    H, W = frame.shape[:2]
    y0 = int(bars["y0"] * H)
    y1 = max(int(bars["y1"] * H), y0 + 1)
    mask = _yellow_mask(frame[y0:y1, :])
    band_h = y1 - y0

    healths = []
    for p in bars["players"]:
        x0 = int(p["x0"] * W)
        x1 = max(int(p["x1"] * W), x0 + 1)
        # a column counts as "filled" if at least half its band rows are yellow
        col_filled = mask[:, x0:x1].sum(axis=0) >= (band_h * 0.5)
        healths.append(float(col_filled.mean()))
    return healths


def calibrate(frame):
    """
    Auto-detect the four bars from a full-health frame.
    Returns a bars dict (same shape as DEFAULT_BARS). Raises if not exactly 4 found.
    """
    H, W = frame.shape[:2]
    mask = _yellow_mask(frame)

    # Bars live in the bottom quarter of the screen. They're the strongest
    # horizontal band of yellow there, so: find the peak row, then expand up
    # and down while rows stay close to peak strength. (This keeps the weaker
    # yellow bits below the bars — gem meters etc. — out of the band, and
    # works whether the match has 2, 3, or 4 players.)
    bottom0 = int(H * 0.75)
    rowcount = mask[bottom0:, :].sum(axis=1)
    peak = int(rowcount.argmax())
    if rowcount[peak] < W * 0.08:
        raise RuntimeError("Couldn't find the health-bar row band — is everyone at full health?")
    thr = rowcount[peak] * 0.6
    lo = peak
    while lo > 0 and rowcount[lo - 1] >= thr:
        lo -= 1
    hi = peak
    while hi < len(rowcount) - 1 and rowcount[hi + 1] >= thr:
        hi += 1
    if hi - lo < 4:
        raise RuntimeError("Health-bar band too thin — is everyone at full health?")
    y0, y1 = bottom0 + lo, bottom0 + hi + 1

    # Find column runs inside the band.
    band = mask[y0:y1, :]
    on = band.sum(axis=0) >= (y1 - y0) * 0.5
    runs, start = [], None
    for x in range(W):
        if on[x] and start is None:
            start = x
        elif not on[x] and start is not None:
            runs.append((start, x))
            start = None
    if start is not None:
        runs.append((start, W))
    runs = [rn for rn in runs if rn[1] - rn[0] > W * 0.02]  # ignore specks

    if not 2 <= len(runs) <= 4:
        raise RuntimeError(f"Expected 2-4 bars, found {len(runs)}: {runs}")

    return {
        "y0": round(float(y0) / H, 4), "y1": round(float(y1) / H, 4),
        "players": [
            {"name": f"{i + 1}P", "x0": round(rn[0] / W, 4), "x1": round(rn[1] / W, 4)}
            for i, rn in enumerate(runs)
        ],
    }


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    frame = cv2.imread(sys.argv[1])
    if frame is None:
        sys.exit(f"Couldn't read image: {sys.argv[1]}")

    if "--calibrate" in sys.argv:
        bars = calibrate(frame)
        with open(CALIB_FILE, "w") as f:
            json.dump(bars, f, indent=2)
        print(f"Saved calibration to {CALIB_FILE}:")
        print(json.dumps(bars, indent=2))
    else:
        bars = load_calibration()

    for p, h in zip(load_calibration()["players"], read_health(frame)):
        print(f"{p['name']}: {h * 100:5.1f}%")

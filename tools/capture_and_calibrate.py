"""
capture_and_calibrate.py — one-shot setup check before training.

Run this ONCE with the game running and a match started (everyone at full
health — pausing is fine, the bars stay visible):

    python capture_and_calibrate.py

It grabs a frame from the exact capture region training will use, saves it as
calibration_shot.png so you can eyeball it, auto-detects the four health bars,
saves the calibration, and prints the health readout. If it prints four
values near 100%, you're ready to run train_ai.py.
"""

import sys
import time

import cv2
import mss
import numpy as np

import health_reader
from powerstone_env import MONITOR

# Grace period: click the game window NOW so the terminal isn't in the shot
print("Capturing in:")
for i in range(5, 0, -1):
    print(f"  {i}...  (click the game window so it's on top)")
    time.sleep(1)

with mss.mss() as sct:
    region = MONITOR if MONITOR is not None else sct.monitors[1]
    frame = np.array(sct.grab(region))[:, :, :3]

cv2.imwrite("calibration_shot.png", frame)
print(f"Captured {frame.shape[1]}x{frame.shape[0]} -> calibration_shot.png")

try:
    bars = health_reader.calibrate(frame)
except RuntimeError as e:
    print(f"\nCalibration FAILED: {e}")
    print("Open calibration_shot.png and check that the game (with all four")
    print("health bars) is fully visible in it. If the game window is cut off,")
    print("run it fullscreen or set MONITOR in powerstone_env.py to the window's")
    print("position, then run this again.")
    sys.exit(1)

import json
with open(health_reader.CALIB_FILE, "w") as f:
    json.dump(bars, f, indent=2)
print(f"Calibration saved to {health_reader.CALIB_FILE}")

print("\nHealth readout:")
for p, h in zip(bars["players"], health_reader.read_health(frame, bars)):
    print(f"  {p['name']}: {h * 100:5.1f}%")
print("\nAll four near 100%?  ->  python train_ai.py")

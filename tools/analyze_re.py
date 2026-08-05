#!/usr/bin/env python3
"""RE mega-take dump analyzer (Power Stone 2 RL, Aug 2026).

Parses the recorder dumps written by powerstone.lua (`record` command):
  H,secs=200,every=10,startframe=NNN,regions=8C474000:4000+...
  S,<frame>,<pressmask>
  R,<chunkbase8hex>,<hexwords...>          (8192 words max per R line)

Loads everything into a (n_snaps, n_words) uint32 matrix plus an addr map,
then offers event-window correlation: find words that changed inside given
frame windows and were quiet outside.
"""
import sys
import numpy as np


def load_dump(path):
    frames = []            # frame number per snapshot
    rows = []              # list of np.uint32 arrays (one per snapshot)
    cur = None
    header = None
    addrs = None           # np.uint32 array of word addresses (built once)
    addr_parts = None
    with open(path) as f:
        for line in f:
            if line.startswith("H,"):
                header = line.strip()
            elif line.startswith("S,"):
                if cur is not None:
                    rows.append(np.concatenate(cur))
                _, fr, mask = line.split(",")
                frames.append(int(fr))
                if addrs is None and addr_parts:
                    addrs = np.concatenate(addr_parts)
                cur = []
                first = addrs is None
                if first:
                    addr_parts = []
            elif line.startswith("R,"):
                _, base, hexdata = line.rstrip("\n").split(",", 2)
                base = int(base, 16)
                n = len(hexdata) // 8
                a = np.frombuffer(hexdata.encode(), dtype="S8", count=n)
                vals = np.array([int(x, 16) for x in a], dtype=np.uint32)
                cur.append(vals)
                if addrs is None:
                    addr_parts.append(base + 4 * np.arange(n, dtype=np.uint32))
    if cur is not None:
        rows.append(np.concatenate(cur))
    if addrs is None:
        addrs = np.concatenate(addr_parts)
    n = min(len(rows[0]), *(len(r) for r in rows))
    mat = np.vstack([r[:n] for r in rows])
    return header, np.array(frames), addrs[:n], mat


def as_float(u32col):
    return u32col.view(np.float32) if u32col.dtype == np.uint32 else \
        np.frombuffer(u32col.astype(np.uint32).tobytes(), dtype=np.float32)


def word_col(addrs, mat, addr):
    # addrs is NOT sorted (regions are recorded in command order), so use
    # exact match, never searchsorted.
    idx = np.nonzero(addrs == np.uint32(addr))[0]
    if len(idx) == 0:
        raise KeyError(hex(addr))
    return mat[:, idx[0]]


def change_mask(mat):
    """(n_snaps-1, n_words) bool: value changed between consecutive snaps."""
    return mat[1:] != mat[:-1]


def active_in_window(frames, mat, f0, f1):
    """Per-word count of changes with END-frame inside [f0, f1]."""
    ch = change_mask(mat)
    sel = (frames[1:] >= f0) & (frames[1:] <= f1)
    return ch[sel].sum(axis=0), ch[~sel].sum(axis=0)


def find_event_words(frames, addrs, mat, windows, quiet_tol=0, min_hits=1):
    """Words that changed in EVERY given window and <=quiet_tol times outside.

    windows: list of (f0, f1) frame ranges. Returns list of
    (addr, [in-window change counts...], outside_count).
    """
    ch = change_mask(mat)
    endf = frames[1:]
    out_sel = np.ones(len(endf), dtype=bool)
    per_win = []
    for f0, f1 in windows:
        sel = (endf >= f0) & (endf <= f1)
        out_sel &= ~sel
        per_win.append(ch[sel].sum(axis=0))
    outside = ch[out_sel].sum(axis=0)
    per_win = np.vstack(per_win)          # (n_windows, n_words)
    ok = (per_win >= min_hits).all(axis=0) & (outside <= quiet_tol)
    hits = []
    for i in np.nonzero(ok)[0]:
        hits.append((int(addrs[i]), per_win[:, i].tolist(), int(outside[i])))
    return hits


def describe(addrs, mat, addr, frames, lo=None, hi=None):
    col = word_col(addrs, mat, addr)
    fcol = as_float(col)
    sel = slice(None)
    if lo is not None:
        sel = (frames >= lo) & (frames <= hi)
        col, fcol, fr = col[sel], fcol[sel], frames[sel]
    else:
        fr = frames
    out = []
    prev = None
    for f, u, x in zip(fr, col, fcol):
        if u != prev:
            out.append(f"f={f} 0x{u:08X} ({x:.3f})")
            prev = u
    return out


if __name__ == "__main__":
    header, frames, addrs, mat = load_dump(sys.argv[1])
    print(header)
    print(f"snaps={len(frames)} words={len(addrs)} "
          f"frames {frames[0]}..{frames[-1]}")
    ch = change_mask(mat).sum(axis=0)
    print(f"words changing >=1x: {(ch >= 1).sum()}  "
          f">=10x: {(ch >= 10).sum()}  >=100x: {(ch >= 100).sum()}")

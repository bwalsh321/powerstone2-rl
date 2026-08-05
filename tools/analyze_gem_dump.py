#!/usr/bin/env python3
"""Find the gem counters in a powerstone.lua memory recording.

Usage (after a clean pickup recording, see HANDOFF.md):
    python analyze_gem_dump.py bridge/ps2_dump.txt [--pickups N] [--max-val 6]

Recording procedure (trainer NOT running, you play the stone-collector):
    echo record 120 0x8C474000 0x4000 0x8C531000 0x3000 > bridge\\ps2_cmd.txt
then start from a FRESH match (0 gems) and pick up stones with YOUR player
only, spaced a few seconds apart. Count your pickups; pass --pickups N.

The dump format (written by powerstone.lua recordSnapshot, ~2 snaps/sec):
    S,<frame>,<pressmask>
    R,<baseaddr_hex>,<concatenated 8-hex-digit u32 words>

A gem counter should: start small, stay <= max-val, change rarely, and step
by +-1 (or +-2/3 if two pickups landed between snapshots). Whatever
increments when you grab stones is YOUR player's counter; the other
player's counter is usually the same offset in the neighboring struct and
will have sat at 0 the whole recording.

Output includes ready-to-paste ps2_addr.txt lines:
    word counter  ->  g2=0x8C5324B0
    byte counter  ->  g2=0x8C4750E4@0     (read8 at addr+offset)
After editing ps2_addr.txt:  echo reloadaddrs > bridge\\ps2_cmd.txt
"""

import argparse
from collections import OrderedDict

# Candidates from the earlier (fumbled) recording — flagged if they reappear.
PRIOR_WORDS = {0x8C5324B0}
PRIOR_BYTES = {(0x8C4750E4, 0), (0x8C475110, 3), (0x8C475124, 2)}


def parse(path):
    """-> list of (frame, {word_addr: u32})."""
    snaps = []
    cur_mem = None
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("S,"):
                parts = line.split(",")
                cur_mem = {}
                snaps.append((int(parts[1]), cur_mem))
            elif line.startswith("R,") and cur_mem is not None:
                _, base, blob = line.split(",", 2)
                base = int(base, 16)
                for i in range(len(blob) // 8):
                    cur_mem[base + 4 * i] = int(blob[8 * i:8 * i + 8], 16)
    # drop a possibly-partial last snapshot
    if len(snaps) >= 2 and len(snaps[-1][1]) < len(snaps[-2][1]):
        snaps.pop()
    return snaps


def counter_like(vals, max_val, max_changes):
    if max(vals) > max_val or min(vals) < 0:
        return None
    changes = [(i, vals[i - 1], vals[i])
               for i in range(1, len(vals)) if vals[i] != vals[i - 1]]
    if not (1 <= len(changes) <= max_changes):
        return None
    if any(abs(n - p) > 3 for _, p, n in changes):
        return None
    ups = sum(1 for _, p, n in changes if n > p)
    return {"changes": changes, "ups": ups, "downs": len(changes) - ups,
            "start": vals[0], "final": vals[-1], "peak": max(vals)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dump")
    ap.add_argument("--max-val", type=int, default=6,
                    help="max plausible gem count (default 6)")
    ap.add_argument("--max-changes", type=int, default=20)
    ap.add_argument("--pickups", type=int, default=None,
                    help="how many stones you actually picked up")
    args = ap.parse_args()

    snaps = parse(args.dump)
    if len(snaps) < 10:
        raise SystemExit(f"only {len(snaps)} snapshots — recording too short?")
    frames = [fr for fr, _ in snaps]
    common = set(snaps[0][1])
    for _, mem in snaps[1:]:
        common &= set(mem)
    print(f"{len(snaps)} snapshots (frames {frames[0]}..{frames[-1]}), "
          f"{len(common)} word addresses in all snapshots\n")

    results = OrderedDict()          # (addr, byte|None) -> info
    for addr in sorted(common):
        words = [mem[addr] for _, mem in snaps]
        w = counter_like(words, args.max_val, args.max_changes)
        if w:
            results[(addr, None)] = w
        for b in range(4):
            byts = [(v >> (8 * b)) & 0xFF for v in words]
            r = counter_like(byts, args.max_val, args.max_changes)
            # skip bytes that just mirror an accepted whole-word counter
            if r and not (w and byts == words):
                results[(addr, b)] = r

    if not results:
        raise SystemExit("no counter-like addresses found — try a higher "
                         "--max-val or check the recording covered pickups.")

    # best first: pure up-counters, then fewest changes
    ranked = sorted(results.items(),
                    key=lambda kv: (kv[1]["downs"], len(kv[1]["changes"])))
    print(f"{len(ranked)} candidates (best first):\n")
    for (addr, b), r in ranked:
        spec = f"0x{addr:08X}" if b is None else f"0x{addr:08X}@{b}"
        prior = ""
        if (b is None and addr in PRIOR_WORDS) or (b is not None and (addr, b) in PRIOR_BYTES):
            prior = "   << PRIOR CANDIDATE"
        match = ""
        if args.pickups is not None and r["ups"] == args.pickups:
            match = f"   << UPS == {args.pickups} PICKUPS"
        print(f"  g?={spec:<22} {r['start']}->{r['final']} peak {r['peak']}  "
              f"+{r['ups']}/-{r['downs']} changes{prior}{match}")
        for i, p, n in r["changes"][:12]:
            print(f"        frame {frames[i]:>8}: {p} -> {n}")
        if len(r["changes"]) > 12:
            print(f"        ... {len(r['changes']) - 12} more")
    print("\nPaste the winner into bridge/ps2_addr.txt as g1=/g2= (g2 = the "
          "bot / P2), then:  echo reloadaddrs > bridge\\ps2_cmd.txt")


if __name__ == "__main__":
    main()

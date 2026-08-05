#!/usr/bin/env python3
"""find_gembase.py A.txt B.txt — locate player logic objects (streaming).

Finds the F+0x1B8/F+0x1BC gem count+mirror pair: equal small values [0..3]
in both dumps at addr and addr+4, CHANGED between dumps, with a valid
form-flag word (0 or 0x00010000) at F = addr-0x1B8 in both dumps.
Ramdump R rows must be address-aligned identical between files (they are:
same lua, full sweep). Prints gembase-ready F anchors.
"""
import sys

def rows(path):
    with open(path) as f:
        for line in f:
            if line.startswith('R,'):
                _, addr, hexs = line.rstrip('\n').split(',', 2)
                yield int(addr, 16), hexs

def words(hexs):
    return [int(hexs[i:i + 8], 16) for i in range(0, len(hexs), 8)]

def main():
    pa, pb = sys.argv[1], sys.argv[2]
    # pass 1: candidate count addresses
    cands = []
    prev_tail = None  # (last addr, last two words) to handle +4 across rows
    for (a_addr, a_hex), (b_addr, b_hex) in zip(rows(pa), rows(pb)):
        assert a_addr == b_addr, "dump rows misaligned"
        if a_hex == b_hex:
            continue
        wa, wb = words(a_hex), words(b_hex)
        n = len(wa)
        for i in range(n - 1):
            va, vb = wa[i], wb[i]
            if va > 3 or vb > 3 or va == vb:
                continue
            if wa[i + 1] == va and wb[i + 1] == vb:
                cands.append(a_addr + i * 4)
    if not cands:
        print("0 candidates — did any pickups happen between dumps?")
        return
    need = set()
    for c in cands:
        need.add(c - 0x1B8)
    # pass 2: fetch flag words
    flags_a, flags_b = {}, {}
    for path, out in ((pa, flags_a), (pb, flags_b)):
        for addr, hexs in rows(path):
            span = len(hexs) // 8 * 4
            for f_addr in need:
                if addr <= f_addr < addr + span:
                    off = (f_addr - addr) // 4
                    out[f_addr] = int(hexs[off * 8:off * 8 + 8], 16)
    print(f"{len(cands)} raw count-pair candidates; validating flag words:")
    hits = 0
    # re-read values for reporting
    vals = {}
    for path, idx in ((pa, 0), (pb, 1)):
        for addr, hexs in rows(path):
            span = len(hexs) // 8 * 4
            for c in cands:
                if addr <= c < addr + span:
                    off = (c - addr) // 4
                    vals.setdefault(c, [None, None])[idx] = \
                        int(hexs[off * 8:off * 8 + 8], 16)
    for c in sorted(cands):
        F = c - 0x1B8
        fa, fb = flags_a.get(F), flags_b.get(F)
        if fa in (0, 0x00010000) and fb in (0, 0x00010000):
            va, vb = vals[c]
            print(f"  F=0x{F:08X}  gems {va} -> {vb}  (count @0x{c:08X})")
            hits += 1
    if not hits:
        print("  none survived flag validation")

if __name__ == '__main__':
    main()

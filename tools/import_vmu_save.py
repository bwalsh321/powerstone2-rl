"""
import_vmu_save.py — inject a downloaded Dreamcast save (.VMS/.VMI or .DCI)
into a Flycast VMU card image (vmu_save_A1.bin), replacing any existing save
with the same name.

Usage:
    python import_vmu_save.py <save.vms | save.vmi | save.dci> [card.bin]

If given a .vmi, the matching .vms next to it is used automatically (and the
in-card filename/size come from the .vmi metadata). Default card path is
Flycast's data\\vmu_save_A1.bin. A .bak backup of the card is written first.

CLOSE FLYCAST BEFORE RUNNING THIS — it rewrites the card on exit.
"""

import datetime
import os
import shutil
import struct
import sys

BLOCK = 512
CARD_BLOCKS = 256
FAT_BLOCK = 254
DIR_FIRST = 253      # directory occupies blocks 253 down to 241
DIR_COUNT = 13
DATA_MAX = 199       # user data lives in blocks 0..199, allocated high->low

FAT_FREE = 0xFFFC
FAT_LAST = 0xFFFA

DEFAULT_CARD = os.path.join(
    os.path.expanduser("~"), "Documents", "Downloads", "flycast-win64-2.6",
    "data", "vmu_save_A1.bin")


def bcd(n):
    return ((n // 10) << 4) | (n % 10)


def load_save(path):
    """Returns (vmu_filename_12bytes, data_bytes)."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".vmi":
        with open(path, "rb") as f:
            vmi = f.read()
        if len(vmi) < 108:
            sys.exit(f"{path} is too short to be a VMI file")
        name = vmi[0x58:0x64]
        vms_path = None
        base = os.path.splitext(path)[0]
        for cand in (base + ".vms", base + ".VMS"):
            if os.path.exists(cand):
                vms_path = cand
                break
        if vms_path is None:
            resource = vmi[0x50:0x58].rstrip(b"\x00 ").decode("ascii", "ignore")
            cand = os.path.join(os.path.dirname(path) or ".", resource + ".vms")
            if os.path.exists(cand):
                vms_path = cand
        if vms_path is None:
            sys.exit("Couldn't find the matching .vms next to the .vmi")
        with open(vms_path, "rb") as f:
            data = f.read()
        print(f"VMI: in-card name {name!r}, VMS: {vms_path} ({len(data)} bytes)")
        return name, data
    if ext == ".dci":
        with open(path, "rb") as f:
            raw = f.read()
        name = raw[4:16]
        data = raw[32:]
        # DCI stores data as 32-bit little-endian swapped in some dumps; the
        # common Nexus format is plain. Heuristic: VMS description block of a
        # data file starts with printable text at 0x00.
        print(f"DCI: in-card name {name!r} ({len(data)} bytes)")
        return name, data
    # bare .vms — assume standard Power Stone 2 name
    with open(path, "rb") as f:
        data = f.read()
    return b"P_STONE2_DAT", data


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    save_path = sys.argv[1]
    card_path = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_CARD

    name, data = load_save(save_path)
    name = name.ljust(12, b"\x00")[:12]
    if len(data) % BLOCK:
        data = data + b"\x00" * (BLOCK - len(data) % BLOCK)
    nblocks = len(data) // BLOCK

    with open(card_path, "rb") as f:
        card = bytearray(f.read())
    if len(card) != CARD_BLOCKS * BLOCK:
        sys.exit(f"{card_path} is not a raw 128KB VMU image")

    fat = list(struct.unpack("<256H", card[FAT_BLOCK * BLOCK:(FAT_BLOCK + 1) * BLOCK]))

    # --- find & remove existing file with the same name -----------------
    def dir_slots():
        for db in range(DIR_FIRST, DIR_FIRST - DIR_COUNT, -1):
            for off in range(0, BLOCK, 32):
                yield db * BLOCK + off

    removed = None
    for slot in dir_slots():
        entry = card[slot:slot + 32]
        if entry[0] in (0x33, 0xCC) and entry[4:16] == name:
            first = struct.unpack("<H", entry[2:4])[0]
            b = first
            while b < CARD_BLOCKS:
                nxt = fat[b]
                fat[b] = FAT_FREE
                if nxt == FAT_LAST or nxt >= CARD_BLOCKS:
                    break
                b = nxt
            card[slot:slot + 32] = b"\x00" * 32
            removed = first
            print(f"Removed existing {name!r} (first block {first})")
            break
    if removed is None:
        print(f"No existing {name!r} on card — adding fresh")

    # --- allocate blocks (high -> low, like a real VMU) -----------------
    free = [b for b in range(DATA_MAX, -1, -1) if fat[b] == FAT_FREE]
    if len(free) < nblocks:
        sys.exit(f"Card full: need {nblocks} blocks, only {len(free)} free")
    chain = free[:nblocks]
    for i, b in enumerate(chain):
        fat[b] = chain[i + 1] if i + 1 < nblocks else FAT_LAST
        card[b * BLOCK:(b + 1) * BLOCK] = data[i * BLOCK:(i + 1) * BLOCK]

    # --- write directory entry ------------------------------------------
    slot = next(s for s in dir_slots() if card[s] == 0x00)
    now = datetime.datetime.now()
    entry = bytearray(32)
    entry[0] = 0x33                      # data file
    entry[1] = 0x00                      # no copy protection
    entry[2:4] = struct.pack("<H", chain[0])
    entry[4:16] = name
    entry[16:24] = bytes([bcd(now.year // 100), bcd(now.year % 100),
                          bcd(now.month), bcd(now.day), bcd(now.hour),
                          bcd(now.minute), bcd(now.second),
                          bcd(now.isoweekday() % 7)])
    entry[24:26] = struct.pack("<H", nblocks)
    entry[26:28] = struct.pack("<H", 0)  # header at block 0 for data files
    card[slot:slot + 32] = entry

    card[FAT_BLOCK * BLOCK:(FAT_BLOCK + 1) * BLOCK] = struct.pack("<256H", *fat)

    shutil.copyfile(card_path, card_path + ".bak2")
    with open(card_path, "wb") as f:
        f.write(card)
    print(f"Done: {name!r} written as {nblocks} blocks starting at {chain[0]}")
    print(f"Card updated: {card_path} (backup: .bak2)")

    # --- verify: list the directory -------------------------------------
    print("\nCard contents now:")
    for s in dir_slots():
        e = card[s:s + 32]
        if e[0] in (0x33, 0xCC):
            print("  ", e[4:16].decode("ascii", "ignore"),
                  f"({struct.unpack('<H', e[24:26])[0]} blocks)")


if __name__ == "__main__":
    main()

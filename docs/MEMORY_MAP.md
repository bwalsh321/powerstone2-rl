# Power Stone 2 (Dreamcast) — Reverse-Engineered Memory Map

Everything below was found empirically with the tools in this repo
(`ramdump` full-RAM diffing, the `record` region recorder, `matscan` /
`stonescan` / `fscan` sweeps, and frame-synced screen recordings), on
**Power Stone 2 (USA)** under **Flycast 2.6**. Dreamcast RAM base is
`0x8C000000` (16 MB). All multi-byte values little-endian; floats IEEE754.

Caveats up front: heap-allocated structures are **frozen by savestates**
(that's what makes per-training-slot calibration possible) but move
between fresh matches. Structures marked FIXED sat at the same address
across every match, roster, stage, and savestate we tested; trust but
verify on your own setup.

## Health block — FIXED

```
0x8C475A04  p1 health   float, full = 1000.0
0x8C475A08  p2 health
0x8C475A0C  p3 health
0x8C475A10  p4 health
```

- `+0x14..+0x20`: the four **white damage-trail bars** — same values,
  delayed exactly 60 frames.
- `+0x34` onward: display **mirrors** (repeat other players' values at
  +0x10 strides). Never read these; they cost us two wrong pins.

## Player render skeletons — FIXED roots

Each character is a chain of 4×4 render matrices (column-major-ish,
right column = (0,0,0,1)): a **shadow matrix** (translation y = 0),
the **root** at shadow+0x42C, then limb matrices at 0x90 stride.
Root translations are the player positions; rotation row 0 is facing.

```
player   shadow        root (matrix base)
P1       0x8C5324FC    0x8C532928
P2       0x8C535E34    0x8C536260
P3       0x8C53976C    0x8C539B98
P4       0x8C53D0A4    0x8C53D4D0        (roots at exact 0x3938 stride)
```

Matrix layout (offsets from root):

```
+0x00 r0x  +0x04 r0y  +0x08 r0z      rotation row 0 = FACING vector
+0x0C/+0x1C/+0x2C = 0x00000000       (right column zeros — scan signature)
+0x30 tx   +0x34 ty (real height)    +0x38 tz
+0x3C = scale-dependent, 1.0f for unscaled characters
```

**Facing is NOT unit length** — the rotation rows carry each character's
model scale (measured: Ayame/Julia ≈ 0.90, Falcon/Ryoma ≈ 1.0,
Jack ≈ 1.13, Gunrock = 1.20). Normalize before using as a direction, and
don't filter matrix scans with a ±1.0 rotation bound (that's how Gunrock
hid from us for an hour). Yaw ≈ atan2(r0x, r0z) + constant offset.

Dead players teleport-park at fixed off-arena spots (y ≈ 780–920) and
freeze. The old "p2x struct" (0x8C535DF8) tracks position but is a
pointer struct, not a matrix — no rotation in it.

## Player logic object (the ledger) — per savestate, P2's FIXED in practice

Heap object per player holding the *game-logic* state. Anchor = the form
flag word F. Offsets (verified on two independent instances):

```
F+0x00   form flag word: bit 0x00010000 SET while transformed
         (low bits vary per player type — test the BIT, not equality)
F+0x35   HUD gem-count BYTE: 0..3; holds 3 THROUGH a transformation,
         drops to 0 when the form ends (P2's instance)
F+0x38   state byte (carrying items / in-form indicator)
F+0x54   held-item DEF POINTER (0 = empty-handed):
         molotov 0x0C50DE24, gatling 0x0C519664, flamethrower 0x0C50A384
F+0x74   form multiplier float: 1.0 → 1.05 while transformed
F+0xA0   form meter float: 100.0 at transform, drains to 0
F+0x100  item id halfword: molotov 0x0300, gatling 0x034A,
         flamethrower 0x031E
F+0x1B8  gem count word + mirror at +0x1BC (word-level counter; on some
         instances lags or differs from the HUD byte — we ship the byte)
```

Our P2 (port 2, Falcon) instance: **F = 0x8C5394E8**, HUD count byte
**0x8C53951D** — stable across every savestate tested (it lives in the
fixed player region). Opponent instances vary; find them by diffing two
`ramdump`s around a known pickup (`tools/find_gembase.py`), or watch a
COM transform with the `watch` overlay command.

There are ALSO per-player count blocks trailing the skeletons (e.g. a
COM ledger at count 0x8C53CFD8 / flag 0x8C53CE2C = count−0x1AC) —
same information, different structure; attribution requires care.

## Entity pool & power stones

Pool of 0x90-stride entity slots. Struct offsets:

```
+0x34  active flag (== 1)
+0x38  status halfword (churns; NOT a type id)
+0x3C  class/instance pointer  ← the identity marker
+0x5C  3×3 spin matrix
+0x8C/+0x90/+0x94  x/y/z floats
```

**Class pointers are per-MODE** (they did not vary per stage in 4P VS,
but 1v1 vs 4P differ completely):

- 1v1 original mode: loose stones `0x0C591000–0x0C592FFF`
  (per-color class triples at 0x178 stride).
- **4P VS mode**: the `0x0C591xxx` entities are *invisible always-active
  spawn-pad records at fixed round-number positions* — phantom stones if
  you trust them. Real loose stones only exist after a knock-out:
  ~1 s flight phase (class `0x0C549xxx`, records go stale after), then a
  pickupable resting phase: **`0x0C594100`** (grounded) and
  **`0x0C596E60`** (hovering). Band `0x0C593000–0x0C598000` captures
  resting stones and excludes the pads.
- Score gems (sparkle shower from hits) = `0x0C54AC90` — ubiquitous,
  never treat as power stones.
- Chests `0x0C5Dxxxx` (ensembles of ~3 entities), items `0x0C61xxxx`,
  sub-second effects `0x0C58xxxx`, projectiles (during specials):
  `0x0C5A7D50, 0x0C5A9850, 0x0C604DE0, 0x0C7ECBC8`.

## Emulator/API gotchas (Flycast 2.6 lua)

- `flycast.emulator.loadState/saveState` are **0-indexed** (UI slot 1 =
  index 0) and must run from the **UI/overlay callback** — calling from
  the vblank callback deadlocks the emulator.
- `flycast.memory.readTable32` returns **garbage without erroring** in
  this build. Use per-address `read32`. This silently poisons any bulk
  scan that trusts it.
- The software pause (Start) freezes the game world but vblank callbacks
  keep running — pause is the way to scan/dump fade-prone entities.
  Opening the Flycast menu halts emulation entirely (bridge goes dark).
- Keep scans ≤ ~2048 reads per vblank and pcall-wrapped, or the emulator
  stutters/dies.

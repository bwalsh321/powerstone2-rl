# RE Mega-Take Findings — Aug 3 2026 (take B: 4P VS, desert → submarine)

Method: 200s memory recording (6 snaps/s, regions 0x8C474000+0x4000,
0x8C531000+0xD000, pool 0x8C3EE000+0x3600) synchronized frame-exact to a
screen recording via the overlay frame counter (Google Meet capture, of all
things). Time mapping: gameframe = 64096 + (video_t − 100s) × 59.94.
Blake played Falcon (1P); Ryoma/Pete/Accel idle humans. Round 1 desert
(KO'd all three at t≈151), menus t≈156–162, round 2 submarine t≈163+.
Two transforms (~t=88–124 and ~t=206–222), missile specials at t≈91, 95,
145, 212, 227, 231. Analysis: `analyze_re.py` + numpy over 1,201 snapshots
× 20,864 words.

## PINNED (verified against on-video events, THIS session's mode)

**All four healths** (floats, full=1000):

```
h1 = 0x8C475A04   (P1 — never dropped; Blake untouched)   [known]
h2 = 0x8C475A08   (P2 Ryoma — died on video both rounds ✓) [known]
h3 = 0x8C475A10   (P3 Pete — r1: 932→863→612→361→0 at t≈149 ✓, r2 reset
                   to 1000 at round load ✓, X'd on video t≈228 ✓)  [NEW]
h4 = 0x8C475A28   (P4 Accel — r1: 661.8→119.8, r2 ground to ~7 ✓)  [NEW]
```

WARNING: 0x8C475A18/20/38/40/48/50/58/60 are DISPLAY MIRRORS (repeat h2/h3
values at +0x10 strides). The old "+0x30 spacing" h3/h4 guess (0x8C475A34/38)
was reading Ryoma's mirror — never use those.

**Facing + real height — the position addresses are a 4×4 render matrix's
translation column.** Matrix base = pos−0x30:

```
P1 matrix 0x8C532928:  +0x00 r0x, +0x04 r0y, +0x08 r0z   (rotation row 0)
                       +0x30 tx (=p1x), +0x34 ty (=p1y, REAL height,
                       −175..+495 seen), +0x38 tz (=p1z), +0x3C = 1.0f
facing: yaw = atan2(r0x, r0z) − 90°; verified vs movement heading,
median residual 12° over 531 moving samples. For obs: feed (r0x, r0z)
raw as the facing unit vector — offset constant is irrelevant to the net.
P2 matrix 0x8C535DC8: translation tracks Ryoma ✓ but rotation rows read 0
this session — VERIFY LIVE in the training savestate before trusting.
P3/P4 matrices: NOT at +0x34A0 stride (those addrs are all-zero); they sit
beyond 0x8C53E000 — extend next recording window to find them.
```

**Projectile classes** (entity pool +0x3C, active ONLY during missile
specials): `0x0C5A7D50, 0x0C5A9850, 0x0C604DE0, 0x0C7ECBC8`.

**Stone-class structure**: two same-stride (0x178!) class triples of
still, hovering entities — per-COLOR stone classes, likely one triple per
stage: `{0x0C549720, 0x0C549898, 0x0C549A10}` (y≈50, desert-era) and
`{0x0C5492B8, 0x0C549430, 0x0C5495A8}` (y≈110). Score gems (the sparkle
shower from hits) = `0x0C54AC90` (5,510 samples, ubiquitous — obs must
NEVER treat these as power stones).

**Class table RELOCATES per mode/stage** — the old desert-1v1 stone band
(0x0C591xxx) never appears in this 4P VS session. Confirms per-stage
stonescan calibration is mandatory, and adds: per-MODE too.

## PLAYER LOGIC OBJECT — CRACKED (Aug 3 late session, ramdump diffing)

Method: new `ramdump <id>` lua command (full 16MB smeared snapshot, ~3 s)
+ held-pose protocol: baseline / carrying stones / TRANSFORMED / post-form
/ molotov / empty / gatling / flamethrower / firing. Diffed 9 snapshots;
words stable across all baselines but changed in exactly one pose class →
the player object, found via the 2KB window containing BOTH transform and
item diffs: **0x8C535800–0x8C536000** (this match's instance).

Field layout (use the FORM FLAG word F as anchor; tonight F=0x8C535BB0):

```
F + 0x00  form flag: 0x00010000 while transformed, else 0   ← THE FLAG
F + 0x04  form flag mirror: 0x00000001 while transformed
F + 0x38  state byte: 0xFF000000 while carrying stones/items, 6 in form
F + 0x54  held-item DEF POINTER (0 when empty-handed):
          molotov 0x0C50DE24, gatling 0x0C519664, flamethrower 0x0C50A384
F + 0x74  form multiplier float: 1.0 → 1.05 while transformed
F + 0xA0  FORM METER float: 100.0 at transform, 0 otherwise (drains)
F + 0x100 ITEM ID halfword ×2: molotov 0x0300, gatling 0x034A,
          flamethrower 0x031E (0x0006 observed while firing)
F + 0x118 item param (ammo?): 0x61C8/0x6410/0x6C08 high-halfword
F + 0x1B4 gem list pointer
F + 0x1B8 GEM COUNT (the real per-player logic counter — counted 3 while
          carrying stones, ticked 1→2 as loose stones were walked over)
F + 0x1BC gem count mirror
```

**CRITICAL CAVEAT — the object is heap-allocated PER MATCH.** Take-B
Falcon's object was NOT at matrix−0x218 (searched: zero) — neither the
object base nor the matrix→object delta is stable across matches or
characters. Savestates freeze allocation, so per-TRAINING-SLOT addresses
are stable once calibrated. **Calibration plan (automatable, no human):**
during normal training on a slot, record a ~60 s window and find the word
whose increments coincide with the env's own pool-derived pickup events →
that's F+0x1B8 → F. Or one supervised transform per slot. Add a
`playerscan` lua command later if wanted: scan for the 0x00010000 flag
word during a transform.

Item-id dictionary so far: 0x0300 molotov, 0x034A gatling, 0x031E
flamethrower. Extend by logging F+0x100 during play — ids arrive free
once the object is anchored per slot.

## Misc

- Stage-boundary pointer candidate: 0x8C53243C (0 → 0x0C500CC4 at round-2
  load). Weak evidence; per-savestate snapshot diffs are the better tool.
- Pause menu on-screen t≈46–49 (menu-flag hunting window in dump A/B if
  ever needed).
- g3/g4 gem counters: idle players never picked up stones — no signal this
  take. Gems v2 (pool-based) doesn't need them.
- 4P VS ≠ 1v1 training savestates: verify h3/h4 + matrix layouts live in
  the training states before wiring into obs.

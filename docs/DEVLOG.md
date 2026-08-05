# Power Stone 2 RL — Development Log / Project Status

_This is the living working document the project was actually run from —
kept verbatim (lightly redacted) because the dead ends are half the value._

_Last updated: Aug 3, 2026 (RE MEGA-TAKE analyzed — see below + `RE_FINDINGS_AUG3.md`).
Read this first in any new session._

**RE STATUS — CORRECTED Aug 3 2026 (live white-bar-lag analysis during
run-3 calibration; supersedes the video-correlation claim below):**
h1..h4 are CONTIGUOUS at 4-byte stride: p1=0x8C475A04, p2=0x8C475A08,
p3=0x8C475A0C, p4=0x8C475A10 (all in ps2_addr.txt). The old "h3=0x8C475A10,
h4=0x8C475A28" was misread: A10 is really h4's slot in the OLD numbering and
A28 is junk. Block layout: +0x14..+0x20 = the four DELAYED white damage-trail
bars (exactly 60 frames behind), +0x34 onward = display mirrors. Never use
anything past +0x10. The known position addrs are the translation column of
per-player 4×4 RENDER MATRICES (base = pos−0x30): facing = (r0x@+0x00,
r0z@+0x08) as a unit vector (yaw = atan2(r0x,r0z)−90°, 12° median residual
vs motion heading), REAL height = +0x34 = the known y addrs (−175..+495
observed — p2y "always 0" was wrong/mode-specific). P1 matrix 0x8C532928,
P2 matrix 0x8C535DC8 (P2's rotation rows read 0 in the 4P session — verify
live in a training savestate before wiring into obs). P3/P4 matrices are
NOT at +0x34A0 stride; they live past 0x8C53E000 (extend the recording
window next time). Projectile classes (4P VS mode): 0x0C5A7D50 /
0x0C5A9850 / 0x0C604DE0 / 0x0C7ECBC8, active only during specials. Stone
classes come as 0x178-stride PER-COLOR TRIPLES; score gems = 0x0C54AC90
(ubiquitous — never treat as power stones). The class table relocates PER
MODE as well as per stage → stonescan calibration stays mandatory.
RESOLVED SAME NIGHT (second session, `ramdump` full-RAM diffing — layout
in RE_FINDINGS_AUG3.md): PLAYER LOGIC OBJECT cracked. Form flag
(0x00010000 word), form meter (100.0 float draining), held-item def ptr +
ITEM ID halfword (dictionary started: molotov 0x0300, gatling 0x034A,
flamethrower 0x031E), and the REAL per-player gem count — all at fixed
offsets from the flag word (F+0x54 item ptr, F+0x74 multiplier, F+0xA0
meter, F+0x100 item id, F+0x1B8 gems). CAVEAT: object is heap-allocated
PER MATCH (take-B Falcon's object was elsewhere; no stable matrix→object
delta) — but savestates freeze allocation → calibrate ONCE per training
slot, automatable: correlate a candidate word's increments with the env's
pool-derived pickup events during normal play → that word is F+0x1B8 →
anchor F. New lua tools shipped for all this: `ramdump <id>` (full 16MB
smeared snapshot ~3 s, D,start/end frame stamps), `record x<every>`,
`recstop`. Dead ends (do NOT retry): posscan = zero bit-identical copies
of render position; fscan 0-centered floods at 2% coverage; player render
matrices move per match too (walk-diff re-finds them in 6 s when needed). The VIDEO-SYNC
METHOD is proven and cheap (overlay frame counter burned into any screen
recording — Google Meet capture worked!): `record <secs> x<every>
<regions>` + `recstop`, map gameframe = anchor + Δt×59.94, montage frames,
correlate. Reuse it for every remaining hunt.

## TL;DR

We train a PPO agent (stable-baselines3) to play Power Stone 2 on Dreamcast via
**standalone Flycast 2.6** on the Windows 11 "super-server" (9700K + 2070 Super,
headless basement PC, accessed via Parsec; Cowork reaches it through the desktop
app's device bridge). The agent reads **game memory** (health, positions) through
a Flycast **Lua bridge** — no pixels. First full run is DONE and successful:

- Run 1 (546k steps vs level-1 Ayame, ep_rew +2.82, "mash-with-aim") is
  RETIRED to `backup_run1/` (model + 56-checkpoint league) — Blake restarted
  fresh Aug 1 2026 so the gem obs + stone-priority reward shape the bot from
  step 0 instead of fighting run 1's kick-everything rut. Run 2 opponent:
  human-set COM (Pride, level 2–3 era). `powerstone_state_ppo.zip` +
  `checkpoints_state/` are the LIVE run-2 artifacts; if the fresh bot still
  devolves into mashing, next lever = action-repeat penalty in the env.
- **Overnight run Aug 1→2 (WITH stone obs, verified 22-field line): 33k →
  568k steps.** ep_rew −15.8 → sustained +5–6 (peak 7.3, NEW reward scale),
  ep_len 1270 → ~950 (winning faster, not stalling). kl ~0.010 steady,
  entropy 1.97 → 1.2 gradual — textbook healthy. Reward has oscillated
  +3–6 since ~200k steps: this opponent looks mostly solved → next move
  is difficulty bump (new slot-1 savestate w/ harder COM, keep training)
  or first live self-play run (see roadmap item 4 prerequisites).

## Architecture (v3 — current)

```
Flycast (powerstone.lua) ──writes──▶ bridge/ps2_state.txt   (per vblank)
                          ◀─reads─── bridge/ps2_cmd.txt     (commands)
Python (powerstone_env_state.py) ⟷ that file bridge ⟷ train_state.py (PPO)
```

- **Obs**: 24-dim state vector (healths, relative position, distance, velocities,
  gem counts, **nearest-loose-stone dx/dz in obs[14:15]** (was the reserved
  facing slots — filled Aug 1 2026 late night, same OBS_DIM so run 2's model
  was NOT orphaned), last-action one-hot). MlpPolicy.
- **Actions**: 8 discrete (4 directions, attack A, jump B, special X, block Y),
  held ACTION_FRAMES=6. Agent is **Player 2**; Lua injects buttons directly.
- **Rewards**: damage dealt/taken (±1.0 per full bar), **win +10 / loss −10
  (raised from ±5 Aug 2 2026 — the GEM_W 1.5 + transform 3.0 escalation let
  a 4-pick transform episode pay +9, out-earning a win; telemetry showed
  losses carrying gem_rew=+9.00. ±10 puts win/loss back on top without
  nerfing the gem gradients. ep_rew_mean not comparable across the change;
  takes effect at next trainer restart)**,
  time −0.001/step, **approach shaping APPROACH_W=1.0** (potential-based:
  reward ∝ distance closed toward opponent; circling nets zero),
  **gem incentives (Blake's design, Aug 1 2026 — stones outrank damage
  per event, win/loss stays on top)**: own stone ±0.5, opponent stone
  ∓0.75 (denial > greed), transform at 3rd pickup ±2.0 extra, −0.005/step
  rent while opp holds 2+. Drops from 3 score NOTHING — transforming
  consumes the stones (3→0), and scoring that reset would reward the bot
  when the opponent transforms / fine it for transforming itself.
  ep_rew_mean is NOT comparable to the +2.82 of run 1 (reward scale changed).
  **Added Aug 1 afternoon (Blake's ask — pickups looked accidental):
  STONE_APPROACH_W potential-based approach-to-nearest-loose-stone
  shaping** (only pays vs a stone that existed last step; spawns/pickups
  can't leak reward), **gated OFF for FORM_STEPS=150 (~15 s) after our 3rd
  pickup**. LIVE-TUNED Aug 2: launched at 0.5, which tanked the run
  −2.9→−9.7 in 80k steps (dense stone gradient beat sparse combat reward;
  bot chased stones instead of defending) → cut to **0.15** and the
  poisoned 1.003M→1.083M segment was rolled back to the pre-shaping
  checkpoint. Also fixed Aug 2: the shaping block shadowed me_now/me_prev
  (health floats) with position arrays → ValueError crash at first
  2-consecutive-step loose stone; position vars now pos_now/pos_prev — transformed = do damage, and form damage is already implicitly
  boosted since form hits deal more per hit. Timer is a heuristic; replace
  with the real transform flag when found (roadmap).
- **Resets**: rotation of 3 methods, leading with the focus-proof one:
  `loadstate 0` via Lua (UI slot 1 = **Lua index 0**!), then pydirectinput F7,
  then mashing A. Per-player health baseline measured at each reset.
- **Speed**: frame-synced stepping + F9 turbo held during training → ~21 fps.

## Key files (all in `C:\Users\Blake\Desktop\PowerStone2AI_RL\`)

| File | Role |
|---|---|
| `powerstone_env_state.py` | ACTIVE v3 env (state-vector obs) |
| `train_state.py` | ACTIVE trainer (PPO, lr 3e-4, ent_coef 0.01, target_kl 0.03, n_steps 2048, batch 64; checkpoints every 10k) |
| `powerstone_env_selfplay.py` | v4 self-play env (frozen roster drives P1) |
| `train_selfplay.py` | v4 self-play trainer (continues the same model) |
| `analyze_gem_dump.py` | offline memory-recording analyzer (found the gems) |
| `powerstone.lua` | Flycast-side bridge + address-finder UI + memory recorder + posscan |
| `bridge/ps2_addr.txt` | Memory addresses (see below) |
| `powerstone_state_ppo.zip` | The trained model (resume from this) |
| `checkpoints_state/` | 54 checkpoints = self-play league roster |
| `powerstone_env.py`, `train_ai.py` | Legacy v2 pixel pipeline — reference only |
| `import_vmu_save.py` | VMU save injector (worked; installed the 100% save) |
| `_to_delete/` | Retired models incl. the collapsed pixel run |

**CRITICAL**: Flycast loads the lua from
`C:\Users\Blake\Documents\Downloads\flycast-win64-2.6\powerstone.lua` —
that copy and the Desktop copy must be updated TOGETHER.

Flycast data dir (`...\flycast-win64-2.6\data\`): `vmu_save_A1.bin` (100% save,
.bak/.bak2 backups), `Power Stone 2 (USA).state` (desert-stage savestate, slot 1).

## Memory addresses (Dreamcast RAM base 0x8C000000, health = float, full 1000.0)

```
p1=0x8C475A04 p2=0x8C475A08 p3=0x8C475A0C p4=0x8C475A10   (4-byte stride!)
p1x=0x8C532958   p1y=0x8C53295C  p1z=0x8C532960
p2x=0x8C535DF8   p2y=0x8C535DFC  p2z=0x8C535E00   (p2y reads 0.0 always — cosmetic)
```

Gem counters PINNED (Aug 1, 2026) and verified live through the bridge:

```
g1=0x8C5324AC@1   g2=0x8C536214@2    (@ = single byte, read8 at addr+off)
```

ENTITY POOL pinned (Aug 1 2026 late night, via the new `fscan` scanner +
three recordings `bridge/dump_pool_aug1{b,c,d}.txt`):

```
pool base 0x8C3EE000, stride 0x90 (28 slots recorded; may extend past 0x1000)
slots 0-3  = the POWER STONE slots (loose stones only ever appeared here)
slots 4+   = chests (multi-slot ensembles), ground items, managers
struct offsets: +0x34 active flag (==1), +0x38 status-flag halfword (churns,
  NOT a type id), +0x3C instance pointer = the real identity marker:
    stones 0x0C591000-0x0C592FFF   (0x0C5911B0/17F8/1A70, 0x0C592258 seen)
    chests 0x0C5Dxxxx   items 0x0C61xxxx   sub-second effects 0x0C58xxxx
  +0x5C 3x3 spin matrix, +0x8C/+0x90/+0x94 = x/y/z floats (loose stones
  hover y~50 and sit still for seconds-to-minutes)
picked-up stones DEACTIVATE their slot -> no carried-stone false positives
```

**STONE VISIBILITY FIX (Aug 2 2026 morning)**: stones knocked OUT of a
player respawn in pool slots BEYOND 0-3 — for its whole life the bot could
only see natural spawns (slots 0-3): knocked-loose stones were invisible
to obs, shaping, and the pickup detector (smoking gun: oploose ≈ 0 across
~800 episodes of v2 telemetry while the bot visibly knocked stones loose
constantly; its rare pickups were walkover luck on natural spawns).
powerstone.lua now sweeps STONE_SCAN_SLOTS=64 pool slots for active
stone-band entities and reports the first 4 found (state line format
unchanged — env/model untouched). AS ALWAYS: sync BOTH lua copies
(project folder + Flycast folder) and restart Flycast + trainer together.
After deploy, confirm with telemetry: oploose should start firing and
picks/ep should finally respond to the greed incentives.

**State line is now v3 (22 fields)**: ...,g1,g2,s0x,s0z,s1x,s1z,s2x,s2z,
s3x,s3z,cmdseq — stone pairs are 0,0 when the slot is empty, cmdseq stays
LAST. Both envs parse v2 (14-field) and v3; but a v3 lua with a v2 env
breaks the ack loop, so as always: **sync both lua copies + both envs
together**. Remote scanner commands (all rewrite `bridge/ps2_fscan.txt`):
`fscan <cx> <cz> <r>`, `ffilter still|moved|near <cx> <cz> <r>`.

**RESOLVED Aug 2 2026 night — g1/g2 counters are MATCHUP-DEPENDENT, do not
trust them.** Vs level-7 Gunrock the ep telemetry logged picked=244/lost=240
in a single episode and gem reward swung −49..+22/ep (win = +5): the bytes
that sawtoothed cleanly in the Ayame/Pride era read churn in other
matchups. (This was the Aug 1 caution — g1 reading 4-5 — grown fatal.)
**GEM SYSTEM v2**: rewards/obs[12:13] now come from the entity pool via
`_stone_gems()` in the env — a tracked stationary stone (age ≥3 steps)
vanishing within 130u of a player = pickup by the closer player; a NEW
stone appearing within 200u of a stone-holder = knock-out, but only
within 5 steps of real damage (stones drop when HIT; respawns near a
camper score nothing). Internal 0-3 counts drive transform bonuses.
g1/g2 still stream in the state line but touch nothing. NOTE: the
selfplay env mirrors obs and still uses raw g1/g2 — port gems v2 to it
BEFORE the first selfplay run.

Gem counters found via two recordings + `analyze_gem_dump.py`: P1's counter sawtoothed
0→3→0 through ~24 pickups/transform resets; P2's confirmed by a flip test
(second controller, only 2P collecting). Notes: the byte above each counter
mirrors it (16-bit-ish field); spacing between the two is NOT the position
struct spacing (+0x34A0) — don't assume struct symmetry here. Red herrings,
do not re-pin: `0x8C533E60` (sat at 1, flat while 2P collected),
`0x8C475210@0` (slow 0→3, not the live count). Raw dumps kept in
`bridge/dump_pickups_aug1.txt` / `dump_p2flip_aug1.txt`.

## Hard-won gotchas (do not relearn these)

1. **Lua `flycast.emulator.loadState(n)` is 0-indexed** (UI slot 1 = index 0) and
   **must run in the overlay/UI callback** — calling from the vblank callback
   deadlocks the emulator. powerstone.lua queues it via `pending_state`.
2. **`flycast.memory.readTable32` is broken** in this build → per-address read32
   "slow mode" fallback (already handled in the lua).
3. **FIXED (Aug 1, 2026): ps2_cmd.txt protocol v2.** Python writes
   `<seq>|<cmd>`; Lua executes only when seq differs from last-seen and NEVER
   deletes the file; Lua echoes last-executed seq as the LAST field of every
   state line (extended line is now 14 fields) and `_send` waits for that ack.
   Seq-less lines (hand-dropped via device_bash) still use delete-after-read.
   Both lua copies + both envs must be on v2 together — a v1/v2 mix breaks
   commands loudly (env prints a warning if the lua looks outdated).
   **v2 corollary (crashed two runs on Aug 1 before the fix)**: because the
   cmd file now lives forever, the lua opens it EVERY vblank, and Windows
   os.replace throws WinError 5 if the rename lands in a read window
   (~once per 20–40 min of stepping). `_send` retries the rename for up to
   STEP_TIMEOUT — do not remove that retry loop.
4. **pydirectinput needs window focus** — that's why resets lead with Lua loadstate.
5. **posscan** must stay ≤2048 words/vblank and pcall-wrapped or it kills the bridge.
6. Cowork device bridge: `device_stage_files` can serve STALE cached copies of
   previously-staged paths → `cp` to a NEW filename on the device first, then stage.
   Writes to `ps2_cmd.txt` via device_bash need a small retry loop (ENOENT race).
7. gymnasium vs old gym: wrap with `VecMonitor(DummyVecEnv(...))`, not `Monitor`.
8. PPO health check: approx_kl should sit ~0.01. The pixel run died from lr 2.5e-4
   default + no entropy bonus (kl → 0.25, entropy collapsed 2.07 → 0.58).

## RUN-3 (v4) BUILD — CODE COMPLETE Aug 3 2026, AWAITING CALIBRATION

Built this morning (Blake's call; offline smoke tests pass, not yet run
live): `powerstone_env_v4.py` (69-dim obs: self w/ facing+real
height+abs pos, 3 nearest-first opponent slots w/ alive flags +
threat-dot, all 4 stone pairs w/ present flags, stage-slot one-hot, last
action, 8 spares; rewards = v3's multi-opponent damage/win + nearest-
opponent approach & gem attribution; only reset-time-active opponents
count — stale h3/h4 can't create phantom unkillables), `train_v4.py`
(fresh model powerstone_v4_ppo, checkpoints_v4/, same hypers), lua v4
(`matscan` two-pass matrix finder w/ moved-flag, `playerbase <slot>
<m1..m4>` persisted in bridge/ps2_players.txt, v4 36-field state line
emitted ONLY for calibrated slots — v3 line and the RUNNING v3 env stay
untouched otherwise), `RUN3_CALIBRATION.md` (per-slot procedure + slot
checklist). BEFORE FIRST v4 RUN: (1) calibrate each STATE_SLOTS slot
(playerbase + stonescan; 4P slots also need p3/p4 healths added to
ps2_addr.txt — deliberately NOT added yet, it would poison the running
v3 env with phantom opponents), (2) verify bot=port2 on the 4P slot,
(3) restart Flycast with the new lua (both copies synced by Claude).
The v3 run can keep training until the moment of switchover.

**RUN-3 CALIBRATED + LAUNCHED Aug 3 2026 (5-restart session — details
matter, read before touching the lua again):**

- **Slot 0 RE-SAVED** (~17:36Z): 4P VS desert, 1P Ayame / 3P Gunrock /
  4P Jack all COM lv6, bot = port 2 Falcon (verified on-screen), full
  health, saved unpaused. The old Pete-lv8 1v1 state is gone.
- **Calibration state (ps2_players.txt / ps2_pool.txt):**
  `playerbase 0 0x8C532928 0x8C536260 0x8C539B98 0x8C53D4D0`,
  `playerbase 2 0x8C532928 0x8C536260 0 0`,
  `stonebase 0 0x8C3EAB20 64 0x0C593000 0x0C598000`,
  `stonebase 2 0x8C3E9BF0 128 0x0C591000 0x0C593000`.
- **PLAYER SKELETON MODEL (big win):** per character: shadow matrix (y=0)
  at base, ROOT at shadow+0x42C (translation +0x30 = the classic pos
  addrs, rotation row r0 = facing), limb matrices at +0x90 stride. The
  four roots sit at EXACT 0x3938 stride: 0x8C532928 (P1), 0x8C536260
  (P2), 0x8C539B98 (P3), 0x8C53D4D0 (P4) — and this allocation held
  IDENTICALLY across the 4P COM savestate, a fresh all-human 4P match,
  and the slot-2 1v1 elevator state (same char lineup => same addresses;
  "matrices move per match" from the RE night appears wrong for players,
  at least with a fixed roster). Dead players teleport-park at ~y=900
  spots and freeze. The old p2x struct (0x8C535DF8) is NOT a matrix
  (pointer struct w/ position copy) — P2's real root is 0x8C536260.
- **FACING IS NOT UNIT:** r0 carries per-character SCALE (Gunrock 1.20,
  Jack ~1.13, Ayame/Falcon ~1.0). powerstone_env_v4.py now normalizes
  (fx,fz) at parse time — keep that if the parser is ever rewritten.
- **matscan BUGS FIXED (why 3 scans silently "0 hits"-ed):**
  (1) readChunk was declared BELOW matscanStep -> nil-global call ->
  pcall swallowed it every vblank. Forward-declared now. (2) use_bulk:
  flycast.memory.readTable32 returns GARBAGE WITHOUT ERRORING in this
  build — it also poisons fscan/posscan in any fresh session until
  something flips it; now hard-disabled (this likely voids the old
  "posscan: zero bit-identical copies" dead-end verdict). (3) signature
  tightened (right column 0,0,0,1 + rotation-row bound ±1.01 + MAX
  16384) — CAVEAT: the ±1.01 bound FILTERS OUT scaled characters
  (that's how Gunrock hid); widen to ±4 next lua pass. (4) new windowed
  variant `matscan 0xLO 0xHI` (~6 s for the player region
  0x8C500000-0x8C560000). Scan aborts now write the real error to
  bridge/ps2_matscan_err.txt — check it FIRST whenever a scan goes quiet.
- **4P STONE SYSTEM IS DIFFERENT (do not trust the 1v1 band there):**
  the 0x0C591xxx-0x0C592xxx entities on the 4P desert are INVISIBLE,
  always-active spawn-pad/infrastructure records at fixed round-number
  positions — reporting them = 4 phantom stones in obs (poison for
  approach shaping). Real loose stones in 4P VS exist only after a
  knock-out: ~1 s FLIGHT phase (class 0x0C549xxx records near
  0x8C3F1xxx — these go STALE after the stone fades, never trust old
  values) then a pickupable RESTING phase (classes seen: 0x0C594100
  grounded y~60, 0x0C596E60 hover y~200). Hence the per-slot class band
  (ps2_pool.txt v2: slot=base,nslots,clo,chi; stonebase now takes
  optional clo chi). Knocked stones are INVISIBLE during flight —
  the env's knock-out attribution may under-fire on slot 0; watch
  oploose in telemetry.
- **Slot 2 stones:** classic band, entities ride the elevator (y≈3550+,
  only x/z reported — fine). Stones come as PER-COLOR ENTITY CLUSTERS
  (2-4 entities per visual stone, the 0x178-triple thing) => the line
  can show one stone 2-3 times; pickup detection keys on position so it
  mostly tolerates this, but picks/ep may read high. Dedupe in the lua
  reporter = future nicety.
- **Ops tricks learned:** software pause (Start) freezes the game world
  but the bridge + scans keep running — pause is the way to scan
  fade-prone stones. loadstate works from any screen incl. pause menus.
  `press 8 6` = Start on port 2.
- bridge/ has session debris: lua_check_*.lua, rec_*.txt, ps2_dump_old*,
  ps2_matscan_capped.txt etc. — safe to sweep to _to_delete/ anytime.

**GEM SYSTEM v3 — LEDGER-BASED (built Aug 3 night, after the phantom-gem
storm: pool-inference on the 4P slot credited the bot 18 picks / 5
"transforms" per episode, avg gem reward +25/ep vs win=+10 — the policy
was being paid 2.5 wins per loss; run-3 attempt #1 killed at 90k steps):**

- Env fixes (attempt-1 postmortem, all live in powerstone_env_v4.py):
  stone DEDUPE (45u — per-color entity pairs), attribution vs ALL
  opponents with a strict 20%-closer margin (ambiguous scrum = nobody
  credited), PICKUP_R_ME=90, and a HARD per-episode gem clamp
  GEM_EP_CAP=8.0 < win/loss 10 (Aug 2's rule, now structural).
- v5 STATE LINE (44 fields): v4's 35 + G1..G4 (REAL per-player gem
  counts from the logic objects, -1 = unanchored) + f1..f4 (real form
  flags 0/1) + cmdseq LAST. Emitted only when the slot has `gembase`
  anchors (ps2_gems.txt, `gembase <slot> <F1..F4>`, 0=unknown; overlay
  shows "state line: v5 (players+gems)"). Env parses 22/36/44.
- Env scoring: when the bot's counter is anchored, gems score from COUNT
  DELTAS (`_counter_gems`) — pickup=count up, knock-loss=count down
  (form-consumption 3->0 exempt), transform=form-flag flip; same for
  anchored opponents. No proximity inference -> no phantoms possible.
  Pool inference remains the fallback; the ep cap applies to both.
  obs[8]=real count/3, obs[9]=real form flag when available (same slots,
  no obs-layout change).
- ANCHORING per slot: `find_gembase.py` (project root) diffs two
  `ramdump` snapshots for the F+0x1B8/1BC count+mirror pair with a valid
  form-flag word at F; attribute F->player by making a KNOWN player pick
  up between dumps (bot: injected presses). Then `gembase <slot> ...`.
  Full procedure in the script docstring.
- **AUG 4 NIGHT — LEDGER ANCHORED + NEW SLOT ROSTER CALIBRATED (v5 live):**
  - **Falcon's REAL ledger, choreographed-verified** (Blake narrated counts
    while paused ramdumps k1/k2/kT/k0 were taken): logic object
    **F=0x8C5394E8** (form MULTIPLIER F+0x74: 1.0->1.05 and form METER
    F+0xA0: 0->100.0 confirmed at RE-night offsets). HUD count = BYTE
    **0x8C53951D** (=F+0x35): 1/2/3-during-form/0; form flag = BIT
    0x00010000 IN F's word (base bits vary — lua uses a bit test).
    Lua reads count via read8. ADDRESS IS SAVESTATE-INDEPENDENT
    (verified live on slots 0 and 1; fixed player region).
  - gembase format: `gembase <slot> <c1> <f1> ... <c4> <f4>` (count addr,
    flag addr per player; 0x0 = unanchored — PLAIN 0 IS REJECTED by the
    arg parser, always write 0x0). All three slots: Falcon at index 2,
    opponents unanchored (opp-side gem scoring silently off until then).
  - **0x8C53CFD8 is a COM's ledger** (near-skeleton block, flag at
    count-0x1AC) — the earlier "Falcon" attribution was wrong (Blake's
    HUD watch disproved it). Opponent anchoring TODO: use `watch` +
    observed COM transforms per slot (~minutes, no dumps).
  - `watch <0xA1> ...` / `watchclear`: overlay live-memory watches
    (hex | int | float per address) — Blake's idea, built Aug 4.
  - Env transform guard: HUD count holds 3 THROUGH a form and drops 3->0
    at form END — knock-loss scoring skips drops while prev-step form==1.
  - **4P stone classes are per-MODE, not per-stage**: desert/elevator/sky
    4P all use resting classes 0x0C594100 (grounded) + 0x0C596E60
    (hover); spawn-pad phantoms (0x0C591xxx) exist on every 4P stage —
    band 0x0C593000-0x0C598000 excludes them. All three slots pinned:
    `stonebase N 0x8C3EAB20 64 0x0C593000 0x0C598000`.
  - **Savestate roster (Aug 4, all COM lv3, bot=port2 Falcon):**
    slot 0 = Ayame/Gunrock/Jack, desert; slot 1 = Ryoma/Julia/Ayame,
    elevator; slot 2 = Wang Tang/Accel/Pride, SKY ("the sky is falling").
    STATE_SLOTS=[0,1,2]. Old 1v1-Pride slot-2 calibration is RETIRED
    (playerbase/pool entries overwritten for the new 4P sky state).
  - Blake's directive: once ledger verified (it now is), stones/transforms
    become the TOP in-play reward — tune GEM_W/TRANSFORM_BONUS UP next
    session, but keep win/loss strictly on top (GEM_EP_CAP < WIN_BONUS
    stays). Sky stage: watch POS_SCALE saturation (stones seen at z=1536).
- **RUN-3a COMPLETE + RETUNE (Aug 5):** first honest-ledger 500k finished
  7:27 AM ET Aug 5: 45/405 wins (11%) vs 3x lv3 (desert 14% / elevator
  17% / sky 2% — sky's edge/hazard problem, see below), dmg 2.5-3.2/ep,
  cap verified (+8.00 flatlines on transform episodes). Model backed up
  as powerstone_v4_ppo_500k_pretune.zip; telemetry archived as
  bridge/ep_stats_v4_run3a_500k.csv. RETUNE applied for run-3b (win #1,
  transform #2, Blake's directive): WIN/LOSS ±15, GEM_W 2.0,
  TRANSFORM_BONUS 6.0 (full cycle +12), GEM_EP_CAP 12, LOST_W 0.75,
  STONE_APPROACH_W 0.5. Known risks: value-fn recalibration noise for
  ~50k steps after resume, kangaroo relapse (watch appr + picks/ep),
  stall incentive from bigger loss penalty (watch ep_len + appr).
  Roll back = restore the pretune zip + revert the 7 constants.
  BACKLOG: sky hazard entity into obs spares (matscan the mover on the
  sky stage); opponent gem anchors via watch+COM-transform observation;
  demonstration recording for behavior cloning ("the Blake prior").
- Roster change pending (Blake, Aug 3 night): difficulty drop to ~lv3,
  3 new savestates, different characters, 2 stages. Player matrix roots
  appear roster-independent (same 4 roots across every match tested) —
  playerbase entries likely copy verbatim; stones need one stonescan per
  NEW stage (desert-4P and elevator bands are banked in ps2_pool.txt);
  gembase must be re-anchored PER NEW SAVESTATE (heap objects).

## Roadmap (agreed order)

1. ~~**Bridge protocol fix**~~ DONE Aug 1, 2026 (see gotcha 3). Lua side
   verified live (state line has 14 fields, legacy seq-less drops worked for
   both gem recordings). Python-side ack loop still untested against live
   Flycast — watch the first `train_state.py` run's reset cycle.
2. ~~**Pin gem counters**~~ DONE Aug 1, 2026 — g1/g2 in ps2_addr.txt (see
   above), verified ticking live in ps2_state.txt while both players grabbed
   stones. Obs slots 12–13 and the `GEM_W=0.5` pickup bonus are live
   automatically; no code changes needed before the next training run.
3. **Difficulty bump** — Ayame to level 2–3 (threshold ep_rew +1.5–2 was passed
   at +2.8). New savestate slot 1 with the harder setup, resume training from
   `powerstone_state_ppo.zip`. Bump again each time ep_rew is solidly +1.5–2.
   train_state.py now trains num_timesteps+500k per run (the old fixed 500k
   target would have been a silent no-op at 546k steps).
4. **Self-play league** — CODE DONE Aug 1, 2026, not yet run live.
   `powerstone_env_selfplay.py` + `train_selfplay.py`: frozen checkpoint
   drives P1 (mirrored obs, one `pressboth <m1> <m2> <frames>` write per
   step), opponent sampled per episode (50% newest / 50% uniform), roster
   re-scanned every reset so new checkpoints join the league live; continues
   powerstone_state_ppo (does not fork). Tested against a simulated bridge
   (obs mirror symmetry, corrupt-checkpoint skip). BEFORE FIRST RUN:
   (a) sync the pressboth lua to the Flycast folder + restart Flycast,
   (b) REDO savestate slot 1 with P1 as a HUMAN player — COM ignores
   injected input.
5. **Items plan (agreed with Blake, Aug 1 2026)** — two tiers:
   - Tier 1, resume-safe: find the HELD-ITEM field on the player struct
     (likely near the gem counter 0x8C5324AC) — record while Blake grabs
     4–5 known items in a memorized order, match ids to times. Then
     per-item pickup rewards in Python (ITEM_REWARDS dict; Power Stone
     Magazine = comedy jackpot, but keep all item rewards < win/loss or
     the bot farms chests instead of fighting). No obs change → can be
     added to a live run.
   - Tier 2 — ENTITY POOL: **FOUND Aug 1 2026 late night** (see the memory
     section above) and the best part came free: nearest-loose-stone dx/dz
     went into the RESERVED obs[14:15] slots, so run 2 continues un-orphaned.
     BEFORE NEXT RUN: copy powerstone.lua to the Flycast folder + restart
     (envs already committed and match the new lua). Still open for a
     future obs-layout change: nearest item/chest + type dictionary
     (instance-ptr bands are known, item ids are not), nearest projectile,
     facing, transform flag. Next reward lever if the bot ignores the new
     obs: small potential-based approach-to-nearest-stone shaping (same
     trick that fixed mash-in-place).
6. **Stage variety (Blake, Aug 1)** — resume-safe any time: save other
   stages into savestate slots 2+, env rotates `loadstate <n>` per episode.
   Player/gem/pool addresses are stage-independent; sanity-check stone
   slots 0-3 once on a hazard stage. Hazard damage already flows through
   health. Do this BEFORE run 3 so 1v1 mastery generalizes across stages.
7. **4-player prep (cheap, before run 3)** — pin p3/p4 positions (guess:
   +0x34A0 multiples past p2's struct, verify live in a 4P match) and
   g3/g4 gem counters (same flip-test as g1/g2). h3/h4 already stream.
8. **Run 3 = the one-time obs change (batch EVERYTHING)**: 3 opponent
   slots (pos/health/vel/gems), facing, transform flag (find the real
   memory flag first — replaces the FORM_STEPS timer), nearest item,
   nearest projectile, maybe stage id. Then the multiplayer curriculum:
   4P free-for-all → 2v2 → **3v1 (three teamed COMs vs the bot — Blake's
   childhood challenge, the final boss)**.
9. ~~Re-verify gem counter semantics~~ RESOLVED Aug 2: counters are
   matchup-dependent noise; superseded by pool-based gem system v2 (see
   memory section). Weights rebalanced same night: greed (pickup 1.0) now
   outranks denial (knock-loose 0.6) — at 0.75-denial/0.5-greed the bot
   learned to chuck cacti at stone-holders forever and never pick one up
   ("the cactus kangaroo", discovered vs level-7 Gunrock). Transform
   bonus 3.0. Port gems v2 to the selfplay env before its first run.
   FIRST TWO TRANSFORMATIONS Aug 2 night vs lv7 Pride (~1.36M and ~1.37M
   steps) — both were wins with record gem hauls (+5.75/+5.25). Picks/ep
   crawled 0.27→~0.4 over ~100 v2 episodes → escalated GEM_W 1.0→1.5 and
   shaping 0.25→0.35 (NOT 0.5 — that collapsed the policy once, but that
   was under counter noise when the walk paid nothing at the end). Watch
   the ep-telemetry appr column after this escalation: appr collapsing
   while shape rises = kangaroo relapse = revert to 1.0/0.25.
10. Other nice-to-haves: real P2 height (search near 0x8C536290), saga
    training-curve chart, engineering-hygiene pass (tests, reproducible
    run configs).
11. **VECTORIZED MULTI-INSTANCE TRAINING (agreed Aug 2 evening — do after
    the RE hunt + run-3 obs land).** Blake has 3-4 PCs; plan: one PPO
    learner, N Flycast instances as a SubprocVecEnv. Per instance: own
    Flycast folder copy (own emu.cfg/savestates/VMU/lua with its own
    bridge DIR; CHD shareable), env gets a bridge-dir parameter,
    optionally pin one STATE_SLOT per env (stage parallelism). Gotchas:
    pydirectinput fallbacks hit the FOCUSED window — secondary instances
    must be pure-lua (disable fallbacks); F9 turbo is focus-held — need
    a frame-limit-off config toggle for unfocused instances (VERIFY in
    Flycast settings; if absent they run 60fps, still additive). Multi-PC:
    emulators remote writing bridge dirs over SMB, learner on super-server
    mounts them; seq/ack protocol already tolerates the latency. Do NOT
    train separate models per PC — PPO weights don't merge. Throughput
    math: ~20 steps/s per bridge → 8 envs ≈ 12M steps/day.
    Sequencing (agreed): tonight's RE hunt (shopping-list recordings) →
    run-3 obs build → THEN scale-out, so the fleet grinds the final obs
    layout instead of a model that gets orphaned by the obs change.
    End state Blake is excited about: self-play league on the fleet, then
    "the Blake gauntlet" (eval mode: P1 = human controller vs the bot) —
    and eventually Blake vs 3 teamed self-play checkpoints.

## Run 3 obs spec — DRAFT v4 (brainstormed Aug 1 2026; refine before building)

Design rules:
- Every optional entity gets an explicit PRESENT flag — (0,0) is a real
  position, not "nothing" (current stone encoding is ambiguous at dx≈0).
- Interactables are egocentric (dx/dz from me); SELF gets ABSOLUTE x,z
  (+y when found) + a stage-id one-hot. Key insight: static stage
  furniture (cacti, hazards, walls) never goes in obs — abs position +
  stage id lets the bot learn stage geometry itself. Only things that
  move/spawn (stones, chests, items, projectiles, players) get entity
  slots.
- Opponent slots sorted NEAREST-FIRST with alive flags (permutation
  invariance: slot 1 = most immediate threat), not fixed player index.
- Reserve ~8 zero slots at the end — the reserved-slot trick just saved
  run 2 from orphaning; always leave room for the next idea.

Blocks (~95–110 dims, fine for MlpPolicy):
- Self: h, abs x,z(,y), vel3, gems, transform flag (+form meter?),
  facing as sin/cos, held-item category one-hot(~6) + present.
- Opponents ×3 (nearest-first): present/alive, rel dx,dy,dz, dist,
  vel3, h, gems, transform flag, threat-dot (cos of their facing vs
  the vector to me — "are they aimed at me" in one float).
- Stones: ALL 4 pool slots as (present, dx, dz) — upgrade from
  nearest-only; data already streams.
- Nearest chest (present, dx, dz); nearest item (present, dx, dz,
  category one-hot ~6 — needs the instance-ptr→type dictionary).
- Nearest projectile (present, dx, dz, vx, vz — velocity IS the dodge
  signal).
- Stage: id one-hot + phase flag (PS2 stages transform mid-match).
- Last-action one-hot (8). Spares (~8).

NOT in obs (on purpose): individual cacti/furniture, raw pool dump,
match timers. Open decision before run 3: VecFrameStack(4) instead of
hand-computed velocities — also an obs change, must ride the same bus.

RE shopping list feeding this spec (each = one scripted recording with
the existing recorder/fscan):
1. Facing — rotate in place, watch player struct (or +0x5C matrix).
2. Real transform flag (+ form timer?) — record across a transform.
3. Held-item id + item-type dictionary — Tier 1 plan (memorized grab
   order).
4. Projectile pool band — fire a gun while recording.
5. Real P2 height — jump repeatedly, search near 0x8C536290.
6. p3/p4 positions + g3/g4 counters — roadmap item 7.
7. Stage id global + phase flag — trivial once per-stage savestates
   exist (stage-variety work feeds this directly).

## Savestate roster (rotation live Aug 2 2026 — STATE_SLOTS=[0,1,2,3])

| Slot (Lua idx) | UI slot | Matchup | Stage | Notes |
|---|---|---|---|---|
| 0 | 1 | Pete lv8 | desert | flagship: stones visible, 5 transforms, ~52% wins (rising) |
| 1 | 2 | Gunrock lv5 | submarine | **BENCHED Aug 2** — stone-blind (gem/shape = exactly 0.0, 49 eps), 6% wins flat; also obs clip saturates (±4600u arena) |
| 2 | 3 | Pride lv5 | elevator | ~86% wins (rising); stones PARTIALLY visible (pool partly survives here) |
| 3 | 4 | Jack lv5 | tomb | **BENCHED Aug 2** — stone-blind, 7% wins flat, taught RETREAT (appr −0.43) |

**Aug 2 2026: STATE_SLOTS trimmed to [0, 2]** (Blake's call after the
per-slot telemetry review at ~1.9M steps / 218 rotation episodes; ep_rew
had plateaued ~+3.1 = "solved 0 and 2, stuck at 0 on 1 and 3"). Losing
per se wasn't the issue — the issue was blind losing: no stone obs/reward
on 1/3 AND no stage id in v3 obs (same obs, different stage, conflicting
gradients), plus the tomb retreat habit sharing weights with everything
else. RE-ADD CHECKLIST for slots 1/3: (1) stonescan-calibrate their pool
bases (workflow below), (2) re-save states at full health (both start
Falcon 10-14% damaged), (3) consider COM level 2-3 instead of 5, (4) put
their indices back in STATE_SLOTS. Adaptive slot sampling (weight toward
the 20-80% win-rate band) was offered and parked as "not yet".

**STONESCAN BUILT (Aug 2 2026, Cowork session — lua only, env/model
untouched, NOT yet deployed/run):**

- `stonescan` command: full-RAM sweep (≤2048 words/vblank, pcall-wrapped
  like fscan) for the stone signature — a word in the stone class band is
  treated as a candidate +0x3C instance ptr; entity base = word−0x3C must
  show active==1 @+0x34 + sane floats @+0x8C/+0x90/+0x94. Hits →
  `bridge/ps2_stonescan.txt` (`entbase,clsptr,x,y,z` + a ready-made
  `# suggest: stonebase <slot> <base> <nslots>` header line). Variant
  `stonescan <0xLO> <0xHI>` overrides the class band in case a stage
  relocates the stone class code (band was pinned on desert).
- `stonebase <slot> <0xADDR> [nslots]` pins a savestate slot's pool base;
  persisted to `bridge/ps2_pool.txt` (`slot=0xADDR,nslots`), reloaded at
  lua startup.
- Every `loadstate <n>` now applies pool_map[n] (default 0x8C3EE000 x64
  when unpinned) — env already sends loadstate per episode, so per-stage
  bases ride for free. Overlay shows `pool 0x... x<slots> slot <n>`.
- Base-derivation trick: all slots of one pool share the same residue mod
  0x90, so the suggested base (lowest hit − 8 slots) is a valid window
  anchor — the true slot-0 address is NOT needed.

**DEPLOY/CALIBRATE WORKFLOW (per stone-blind stage, ~2 min each):** sync
BOTH lua copies + restart Flycast; `loadstate <slot>`; play/wait until a
loose stone is ON SCREEN (knock one out of a player to be sure); send
`stonescan`; wait for "stonescan: N hits" in the overlay (~35–100 s);
read ps2_stonescan.txt, sanity-check hit coords against where stones
visibly are, then send the suggested `stonebase` line. Do slots 1 (sub)
and 3 (tomb); re-check slot 2 (elevator, partial visibility). If 0 hits
with stones visible → retry with the wide band variant.

Still open from the stage bundle: POS_SCALE/clip saturation on big stages
(submarine ±4600u). Slots 1/3 were saved with Falcon ~10-14% damaged
(dmg_in maxes at −0.86/−0.90) — re-save at full health when convenient.
Slot telemetry: ep_stats.csv gained a 15th column = slot index (rotation
era only).

## Session workflow reminders

- Blake runs training himself in a terminal on super-server (`python train_state.py`);
  training auto-resumes from `powerstone_state_ppo.zip` if present.
- Setup per session: launch Flycast (lua auto-loads, BRIDGE ACTIVE window),
  start a fresh match with the right settings, save state to slot 1, run trainer.
  Game window must be visible/focused for the F-key fallbacks and turbo.
- Match settings used: original mode 1v1, P1 = COM Ayame, P2 = the bot,
  damage/timer per current curriculum.
- Blake prefers fast iteration: kill a broken run early over letting it finish.
  Suggest better architectures proactively — he asked for that explicitly.

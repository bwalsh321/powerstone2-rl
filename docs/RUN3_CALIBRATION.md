# RUN-3 (v4 obs) — Per-Savestate Calibration

The v4 env streams positions + FACING from per-player 4×4 render matrices.
Matrix bases are heap-allocated per match, but a savestate freezes them —
so each savestate slot gets a one-time calibration, persisted in
`bridge/ps2_players.txt`. Until a slot is calibrated the lua emits the v3
line for it (the old env keeps working; the v4 env warns and degrades).

Claude drives all of this over the bridge — Blake only needs Flycast
running with a match loaded. ~5 min per slot.

## Per-slot procedure

1. `loadstate <slot>` (lua idx! UI slot − 1). Let the match run unpaused —
   COMs must be moving around.
2. Send `matscan`. Two passes (~70 s + 1 s settle): pass 1 sweeps RAM for
   the matrix signature, pass 2 re-reads translations — anything that
   MOVED between passes is a live actor. Overlay shows progress; result in
   `bridge/ps2_matscan.txt` (`base,x,y,z,moved`).
3. Identify the player matrices among the movers:
   - The bot's / P1's are known on the classic slots: the matrix whose
     translation x sits at the old addrs (`0x8C532958` p1, `0x8C535DF8`
     p2) — i.e. base = addr − 0x30.
   - Remaining persistent movers with plausible ground coords = the other
     players. Junk movers (projectiles, effects) vanish across scans — a
     second matscan a minute later keeps only persistent actors.
   - Map matrix→player-index by health: watch one player take damage
     (h1..h4 in the state line) while noting which matrix is at that
     character's screen position; or just accept an arbitrary 3↔4 order —
     obs sorts nearest-first, only the health pairing must be right.
4. Pin: `playerbase <slot> <m1> <m2> <m3> <m4>` (0 for absent players —
   1v1 slots get m3=m4=0). Persists to ps2_players.txt; overlay flips to
   "state line: v4 (players pinned)" after the next loadstate.
5. Stones: if the slot hasn't been stonescan'd, do it now (stone on
   screen → `stonescan` — for 4P-mode slots use the band override
   `stonescan 0x0C549000 0x0C54A000` per the Aug 3 class fingerprint) and
   send the suggested `stonebase` line.
6. Sanity: watch `bridge/ps2_state.txt` — 36 fields, facing floats
   (~±1) moving as characters turn, h3/h4 sane for 4P slots.

## Health addresses (CORRECTED during calibration, Aug 3 afternoon)

The RE-night values were wrong. True layout — contiguous 4-byte stride,
already in ps2_addr.txt:

```
p3=0x8C475A0C
p4=0x8C475A10
```

+0x14..+0x20 are the four white damage-trail bars (60 frames delayed);
+0x34 onward are mirrors; 0x8C475A28 is junk. Never use any of them.

## Slot status

| Lua idx | Matchup | playerbase | stonescan | notes |
|---|---|---|---|---|
| 0 | 4P desert lv6 (Ayame/Gunrock/Jack) | ☑ 2928/6260/9B98/D4D0 | ☑ 0x8C3EAB20 x64, band 593000-598000 | bot=port2 verified; state RE-SAVED Aug 3 17:36Z |
| 2 | Pride lv5 elevator | ☑ 2928/6260/0/0 | ☑ 0x8C3E9BF0 x128, band 591000-593000 | cluster dupes in line (per-color triples) |

CALIBRATION DONE Aug 3 — both slots emit the 36-field v4 line, verified
live. See HANDOFF.md "RUN-3 CALIBRATED" section for the full findings
(skeleton model, 0x3938 root stride, per-slot stone bands, matscan fixes,
phantom spawn pads in 4P, scale-carrying facing rows).

NOTE the 0x0C549xxx band in the old slot-0 row was wrong — those are
static spawn-manager records + in-flight knock-out records, not resting
stones. Resting = 0x0C593000-0x0C598000 on the 4P desert.

## Env expectations recap

- `PowerStoneEnvV4.STATE_SLOTS` must list ONLY calibrated slots.
- Bot is assumed port 2 (h2 + matrix 2). Verify per 4P slot: press a
  button via `press 4 6` — the character that attacks is the bot.
- Transform flag is still the FORM_STEPS timer heuristic; the real
  logic-object flag needs per-slot anchoring (gem-count correlation
  during play — see RE_FINDINGS_AUG3.md) and one of the 8 obs spares is
  reserved for it.

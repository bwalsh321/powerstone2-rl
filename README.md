# Power Stone 2 RL — teaching a bot to fight (and transform) on a real Dreamcast game

To be clear, yes Claude has helped me scan memory values, and AI has assisted in this project. I am a developer with 3 small kids, one of which is a two week old. There is also no decomp that I could find as this game does not have the same following as Pokemon or other huge games. I used AI to help accelerate this project as memory was the holdup, and what a great job for AI to do instead of me.

This is far from AI slop, I used it to accelerate, not do everything. All the engieering decisions and directions are from me. If I left it up to Claude, it would not have even verified the memory values to see when a powerstone was actually picked up or not. 

A reinforcement-learning rig that trains a PPO agent to play **Power
Stone 2** running in the **Flycast** Dreamcast emulator — no pixels, no
frame grabs. A Lua script inside the emulator reads game memory every
vblank (positions, facing, healths, loose power stones, the bot's real
stone count and transformation flag) and streams it over a file bridge
to a Gymnasium environment; button presses flow back the same way.

The interesting parts, honestly, are the reverse engineering and the
reward-design faceplants. Both are documented.

```
Flycast 2.6 (powerstone.lua) ──writes──▶ bridge/ps2_state.txt   (every vblank)
                             ◀──reads─── bridge/ps2_cmd.txt     (commands/presses)
Python (powerstone_env_v4.py)  ⟷  file bridge  ⟷  train_v4.py (PPO, SB3)
```

## What the agent sees (69-dim observation)

Self (health, absolute position, real height, velocity, **facing**, stone
count from the game's own ledger, transform flag) · three opponent slots
sorted nearest-first (alive, egocentric position, velocity, health, and a
threat-dot: "are they aimed at me") · all four loose-stone slots with
present flags · a stage one-hot · last action · 8 reserved spares so the
next discovery doesn't orphan the model.

## What it's rewarded for

Damage dealt/taken, win/loss on top, potential-based approach shaping,
and a **stone economy read from the game's real per-player gem counter**
— pickups, knock-losses, and transformations score from a ledger, not
from proximity inference. The dev log documents why that matters: every
inferred version eventually paid the bot phantom rewards (best failure
mode name in the project: *the cactus kangaroo*), and a per-episode gem
cap now guarantees no stone economy ever outbids winning.

## Repo tour

| Path | What |
|---|---|
| `powerstone.lua` | The whole emulator side: state line, button injection, savestate control, and an RE toolkit (`matscan`, `stonescan`, `fscan`, `ramdump`, `record`, `watch` overlay) |
| `powerstone_env_v4.py` | Gymnasium env: v3/v4/v5 state-line parsing, obs, rewards, per-slot savestate rotation |
| `train_v4.py` | PPO trainer (stable-baselines3), checkpointing |
| `tools/` | RE helpers: `find_gembase.py` (ledger anchoring via ramdump diffs), dump analyzers, VMU save importer |
| `docs/MEMORY_MAP.md` | **The reverse-engineered memory map** — health block, render-matrix skeletons, the player logic object, entity pool, stone classes per mode |
| `docs/DEVLOG.md` | The living project log, kept verbatim — dead ends included on purpose |
| `docs/RE_FINDINGS_AUG3.md` | The frame-synced video↔memory correlation session that cracked facing and the logic object |
| `docs/RUN3_CALIBRATION.md` | Per-savestate calibration procedure |

## Setup (bring your own game)

No ROM, savestates, BIOS, or emulator binaries are included — you need
your own legally dumped copy of Power Stone 2 and [Flycast](https://github.com/flyinghead/flycast) 2.6.

1. `pip install -r requirements.txt`
2. Put `powerstone.lua` where your Flycast loads Lua scripts and edit the
   `CONFIG` block at the top: point `DIR` at this repo's `bridge/`
   directory (create it).
3. Launch Flycast + the game — you should see the `PS2 RL / BRIDGE
   ACTIVE` overlay.
4. Set up a training match (the agent injects **port 2**; leave P2 as a
   human-controlled port, COM opponents on the rest) and save state to
   slot 1. Calibrate the slot per `docs/RUN3_CALIBRATION.md` — matrix
   roots and the stone pool are found with the lua's built-in scanners
   in a few minutes.
5. `python train_v4.py`. Training auto-resumes from checkpoints.

## Status

Actively training. The current run is the first on the honest ledger —
curves and clips to follow. The roadmap in the dev log runs through
self-play leagues, multi-instance fleets, and eventually "the gauntlet":
one human (hi) versus three trained checkpoints.

## License

MIT — see `LICENSE`. Power Stone 2 is © Capcom; this project contains no
game assets and is not affiliated with Capcom.

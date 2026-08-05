# Power Stone 2 RL — v2 (Flycast Lua bridge) setup

One-time setup, ~10 minutes. After this, training is one command.

## 1. Point Flycast at the Lua script
Flycast > Settings > Advanced > Lua Scripting -> browse to `powerstone.lua`
in this folder. Restart Flycast. A small "PS2 RL" window appears in the
top-left corner of the game screen.

## 2. Set the controller ports (once)
Settings > Controls: your physical controller = Player 1 only.
Nothing else needed — the AI's inputs are injected by the Lua script
directly as Player 2. No virtual controller anymore.

## 3. Find the health addresses (once)
1. Boot Power Stone 2, start a 1v1: 1P = COM level 1, 2P = human (the bot).
2. In the PS2 RL window click **Scan** (takes a few seconds).
3. Let one player take a hit, click **Filter (someone took damage)**.
   Repeat after another hit if there are still more than ~10 candidates.
4. Each remaining candidate shows a live health bar. Take a hit with a
   specific player, watch which bar drops, and click that row's **P1** /
   **P2** button accordingly (P1 = the COM, P2 = the bot).
5. Click **SAVE + start bridge**. The window switches to "BRIDGE ACTIVE"
   with live health bars. Done forever — it auto-loads next launch.

## 4. Save the reset point (once per session)
With a fresh full-health match on screen, save state to **slot 1**
(bind Save State in Flycast controls, or menu > Save State).
Every episode reset reloads this instantly.

## 5. Train
    python train_ai.py
Click the game window during the countdown (the screen is still the
observation, so keep the game fullscreen, visible, unobstructed).

## Turbo (optional, after one clean run)
Bind **Fast-forward** in Flycast's controls and hold/toggle it during
training. The env is frame-synced: faster emulation = proportionally
faster training, with identical game dynamics for the agent.

## Files
- `powerstone.lua`   — Flycast-side: memory reads, button presses, savestates
- `powerstone_env.py`— Python-side: gym env, rewards from real health values
- `bridge/`          — created automatically; the two sides talk through it
- `train_ai.py`      — unchanged: PPO + checkpoints + auto-resume

## Old v1 files (pixel pipeline) — no longer used
`health_reader.py`, `capture_and_calibrate.py`, `health_bars.json`,
`input_controller.py` (vgamepad). Kept for reference; safe to delete.

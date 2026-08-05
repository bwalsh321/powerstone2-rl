"""RUN-3 trainer (v4 obs: multi-opponent, facing, height, stage one-hot).

FRESH model (powerstone_v4_ppo) — the 24-dim v3 model is architecturally
incompatible (69-dim obs) and stays untouched; run it in parallel elsewhere
or retire it once v4 overtakes.

PREREQ per savestate slot in PowerStoneEnvV4.STATE_SLOTS: playerbase
calibration (v4 state line) + stonescan pool calibration. See
RUN3_CALIBRATION.md. The env falls back to the v3 line (no facing, 1
opponent) with a loud warning — fine for smoke tests, wrong for real runs.
"""

import os
import time

from stable_baselines3 import PPO
from stable_baselines3.common.callbacks import CheckpointCallback
from stable_baselines3.common.vec_env import DummyVecEnv, VecMonitor

from powerstone_env_v4 import PowerStoneEnvV4

EXTRA_TIMESTEPS = 500_000
MODEL_PATH = "powerstone_v4_ppo"

env = VecMonitor(DummyVecEnv([lambda: PowerStoneEnvV4()]))

LEARNING_RATE = 3e-4   # target_kl below is the safety brake

if os.path.exists(MODEL_PATH + ".zip"):
    print(f"Resuming from {MODEL_PATH}.zip")
    model = PPO.load(MODEL_PATH, env=env,
                     custom_objects={"learning_rate": LEARNING_RATE,
                                     "lr_schedule": lambda _: LEARNING_RATE})
else:
    model = PPO(
        "MlpPolicy",
        env,
        verbose=1,
        tensorboard_log="./powerstone_logs",
        n_steps=2048,
        batch_size=64,
        learning_rate=LEARNING_RATE,
        ent_coef=0.01,
        target_kl=0.03,
    )

checkpoint = CheckpointCallback(
    save_freq=10_000, save_path="./checkpoints_v4", name_prefix="ps_v4")

print("Training starts in:")
for i in range(5, 0, -1):
    print(f"  {i}...  (click the game window NOW — match running, unpaused)")
    time.sleep(1)

try:
    target = model.num_timesteps + EXTRA_TIMESTEPS
    print(f"Training {model.num_timesteps:,} -> {target:,} steps")
    model.learn(total_timesteps=target, callback=checkpoint,
                reset_num_timesteps=False)
finally:
    model.save(MODEL_PATH)
    env.close()
    print(f"Model saved to {MODEL_PATH}.zip")

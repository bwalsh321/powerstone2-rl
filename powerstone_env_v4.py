"""
Power Stone 2 RL environment, v4 — RUN-3 OBS (multi-opponent edition).

Everything the Aug 3 2026 RE mega-take pinned, wired into one observation:

- SELF: health, ABSOLUTE x,z + real height y, velocity, FACING unit vector
  (render-matrix rotation row 0), internal gem count, form-timer heuristic.
- OPPONENTS x3, sorted NEAREST-FIRST (permutation invariance: slot 0 is
  always the most immediate threat), each with an alive flag, egocentric
  dx,dz,dy, distance, velocity, health, and a threat-dot (their facing
  aimed at me = 1). Absent/dead opponents zero out.
- STONES: all 4 pool pairs as (present, dx, dz) — upgrade from nearest-only.
- STAGE: savestate-slot one-hot (abs position only means something once the
  net knows which arena it's in). No memory address needed — the env knows
  which slot it loaded.
- Last-action one-hot + 8 reserved spares (the reserved-slot trick saved
  run 2 from orphaning; always leave room for the next idea).

Bridge: requires the v4 state line (36 fields) for full data — the lua
emits it when the current savestate slot has calibrated player-matrix
bases (`playerbase` / ps2_players.txt, found via `matscan`). Falls back
to parsing the v3 line (22 fields): 2 players, no facing (zeros).

Rewards are v3's (already multi-opponent for damage and win-when-all-dead)
plus: approach shaping targets the NEAREST ALIVE opponent, and gem-v2
pickup attribution tests the bot against the nearest opponent instead of a
fixed one. Only opponents ACTIVE AT RESET (pinned matrix + healthy
baseline) count for rewards/win — stale h3/h4 bytes on 1v1 slots can't
create phantom unkillable opponents.
"""

import os
import random
import time

import gym
import numpy as np
from gym import spaces

try:
    import pydirectinput
    pydirectinput.PAUSE = 0
except ImportError:
    pydirectinput = None

BRIDGE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge")
STATE_FILE = os.path.join(BRIDGE_DIR, "ps2_state.txt")
CMD_FILE = os.path.join(BRIDGE_DIR, "ps2_cmd.txt")

DC_B, DC_A = 0x2, 0x4
DC_UP, DC_DOWN, DC_LEFT, DC_RIGHT = 0x10, 0x20, 0x40, 0x80
DC_Y, DC_X = 0x200, 0x400

POS_SCALE = 1000.0
VEL_SCALE = 50.0
HEIGHT_SCALE = 500.0      # real height observed -175..+495 in the RE take


class PowerStoneEnvV4(gym.Env):
    AGENT_PLAYER = 2          # bot is the port-2 human side (verify per slot!)
    LOAD_STATE_KEY = "f7"
    TURBO_KEY = "f9"

    # Only CALIBRATED slots belong here (playerbase pinned + stonescan'd).
    # Start with whatever RUN3_CALIBRATION.md has checked off.
    STATE_SLOTS = [0, 1, 2]   # Aug 4 night roster: desert/elevator/sky, all 4P lv3

    N_OPP = 3                 # opponent slots in obs (4P max)

    ACTION_FRAMES = 6
    STEP_TIMEOUT = 5.0

    DAMAGE_DEALT_W = 1.0
    DAMAGE_TAKEN_W = 1.0
    WIN_BONUS = 15.0     # Aug 5 retune: headroom above a maxed gem episode
    LOSS_PENALTY = 15.0
    TIME_PENALTY = 0.001
    APPROACH_W = 1.0
    STONE_APPROACH_W = 0.5  # watch appr for kangaroo relapse (honest ledger now)
    FORM_STEPS = 150          # heuristic until per-slot logic-object anchors
    GEM_W = 2.0           # one stone > two bars of damage
    OPP_GEM_W = 0.75
    KNOCK_W = 0.6
    LOST_W = 0.75
    TRANSFORM_BONUS = 6.0 # biggest non-terminal payout; full cycle = +12
    OPP_TRANSFORM_PEN = 3.0
    DANGER_W = 0.005
    STONE_MATCH_TOL = 40.0
    STONE_MIN_AGE = 3
    PICKUP_R = 130.0
    KNOCK_R = 200.0
    # Aug 3 evening fix — slot-0 phantom gem storm (18 picks / 5 "forms"
    # per ep, gem_rew +25 avg while losing every game):
    GEM_EP_CAP = 12.0         # |cumulative gem reward per episode| ceiling,
                             # strictly < WIN_BONUS: wins stay on top
    PICKUP_MARGIN = 0.8      # credit requires STRICTLY closest by 20%
    PICKUP_R_ME = 90.0       # tighter radius for OUR pickup credit
    STONE_DEDUPE_TOL = 45.0  # per-color entity pairs -> one visual stone
    MAX_STEPS = 6000

    ACTIONS = [
        ("up", DC_UP), ("down", DC_DOWN), ("left", DC_LEFT), ("right", DC_RIGHT),
        ("attack", DC_A), ("jump", DC_B), ("special", DC_X), ("block", DC_Y),
    ]

    # --- obs layout -------------------------------------------------------
    # self:      [0]h [1]absx [2]absz [3]height [4]vdx [5]vdz [6]fx [7]fz
    #            [8]gems/3 [9]form_norm                              (10)
    # opp k=0..2 base=10+9k: [+0]alive [+1]dx [+2]dz [+3]dy [+4]dist
    #            [+5]vdx [+6]vdz [+7]h [+8]threat_dot                (27)
    # stones k=0..3 base=37+3k: [+0]present [+1]dx [+2]dz            (12)
    # stage one-hot: [49..52]                                        (4)
    # last action:   [53..60]                                        (8)
    # spares:        [61..68] always 0                               (8)
    OBS_DIM = 69
    _OPP0, _STN0, _STG0, _ACT0 = 10, 37, 49, 53

    def __init__(self):
        super(PowerStoneEnvV4, self).__init__()
        os.makedirs(BRIDGE_DIR, exist_ok=True)
        self.action_space = spaces.Discrete(len(self.ACTIONS))
        self.observation_space = spaces.Box(
            low=-5.0, high=5.0, shape=(self.OBS_DIM,), dtype=np.float32)

        self.steps = 0
        self.prev = None
        self.prev_health = None
        self.last_action = 0
        self._form_timer = 0
        self._ep = self._fresh_ep()
        self._stone_tracks = []
        self._my_g_int = 0
        self._opp_g_int = 0
        self._hurt_cd = 0
        self._dealt_cd = 0
        self._seq = 0
        self._active_opp = [0]        # player indices that count this episode
        self._warned_v3 = False
        self._send(f"player {self.AGENT_PLAYER}")
        self._wait_for_bridge()
        if self.TURBO_KEY and pydirectinput is not None:
            pydirectinput.keyDown(self.TURBO_KEY)
            print(f"[env] holding {self.TURBO_KEY.upper()} (fast-forward) for this run")

    # ------------------------------------------------------------- bridge IPC

    def _send(self, cmd):
        self._seq += 1
        tmp = CMD_FILE + ".tmp"
        with open(tmp, "w") as f:
            f.write(f"{self._seq}|{cmd}\n")
        deadline0 = time.time() + self.STEP_TIMEOUT
        while True:
            try:
                os.replace(tmp, CMD_FILE)
                break
            except PermissionError:
                if time.time() > deadline0:
                    raise
                time.sleep(0.003)
        deadline = time.time() + self.STEP_TIMEOUT
        while time.time() < deadline:
            s = self._parse_state_once()
            if s is not None and s["ack"] == self._seq:
                return
            time.sleep(0.002)
        print(f"[env] WARNING: no bridge ack for '{cmd}' (seq {self._seq})")

    def _parse_state_once(self):
        """Parse the v4 (36-field) or v3 (22-field) state line.

        Returns dict with: frame, h[4], players[4] = {pos(3), face(2)},
        stones [(x,z)...], v4 flag, ack.
        """
        try:
            with open(STATE_FILE) as f:
                parts = f.read().strip().split(",")
            v = [float(p) for p in parts]
            players = [{"pos": np.zeros(3), "face": np.zeros(2)}
                       for _ in range(4)]
            if len(v) == 44:                       # v5: v4 + real counters
                h = [max(0.0, x) for x in v[1:5]]
                for k in range(4):
                    o = 5 + 5 * k
                    players[k]["pos"] = np.array([v[o], v[o + 1], v[o + 2]])
                    face = np.array([v[o + 3], v[o + 4]])
                    n = float(np.hypot(face[0], face[1]))
                    players[k]["face"] = face / n if n > 1e-6 else face
                    players[k]["gems"] = int(v[35 + k])   # -1 = unanchored
                    players[k]["form"] = int(v[39 + k])
                stones = []
                for k in range(4):
                    x, z = v[27 + 2 * k], v[28 + 2 * k]
                    if x != 0.0 or z != 0.0:
                        stones.append((x, z))
                return {"frame": int(v[0]), "h": h, "players": players,
                        "stones": stones, "v4": True, "v5": True,
                        "ack": int(v[43])}
            if len(v) == 36:                       # v4
                h = [max(0.0, x) for x in v[1:5]]
                for k in range(4):
                    o = 5 + 5 * k
                    players[k]["pos"] = np.array([v[o], v[o + 1], v[o + 2]])
                    face = np.array([v[o + 3], v[o + 4]])
                    # normalize: render matrices carry per-character SCALE
                    # (Gunrock r0 norm = 1.2, verified Aug 3) — facing must
                    # be a unit vector or his fx/fz and threat-dot skew
                    n = float(np.hypot(face[0], face[1]))
                    players[k]["face"] = face / n if n > 1e-6 else face
                stones = []
                for k in range(4):
                    x, z = v[27 + 2 * k], v[28 + 2 * k]
                    if x != 0.0 or z != 0.0:
                        stones.append((x, z))
                return {"frame": int(v[0]), "h": h, "players": players,
                        "stones": stones, "v4": True, "ack": int(v[35])}
            if len(v) == 22:                       # v3 fallback (no facing)
                h = [max(0.0, x) for x in v[1:5]]
                players[0]["pos"] = np.array(v[5:8])
                players[1]["pos"] = np.array(v[8:11])
                stones = []
                for k in range(4):
                    x, z = v[13 + 2 * k], v[14 + 2 * k]
                    if x != 0.0 or z != 0.0:
                        stones.append((x, z))
                return {"frame": int(v[0]), "h": h, "players": players,
                        "stones": stones, "v4": False, "ack": int(v[21])}
        except (OSError, ValueError, IndexError):
            return None
        return None

    def _read_state(self):
        deadline = time.time() + self.STEP_TIMEOUT
        while time.time() < deadline:
            s = self._parse_state_once()
            if s is not None:
                return s
            time.sleep(0.002)
        raise RuntimeError("No data from the Lua bridge — is Flycast running "
                           "with powerstone.lua and a game loaded?")

    def _wait_for_bridge(self):
        s1 = self._read_state()
        time.sleep(0.3)
        s2 = self._read_state()
        if s1["frame"] == s2["frame"]:
            raise RuntimeError("Bridge frame counter not advancing — emulator paused?")
        print(f"[env] bridge alive (frame {s2['frame']}, "
              f"line={'v4' if s2['v4'] else 'v3 fallback'})")

    def _wait_frames(self, start_frame, n):
        deadline = time.time() + self.STEP_TIMEOUT
        while time.time() < deadline:
            s = self._read_state()
            if s["frame"] >= start_frame + n or s["frame"] < start_frame:
                return s
            time.sleep(0.002)
        return self._read_state()

    # ---------------------------------------------------------------- gym API

    def reset(self):
        self.steps = 0
        slot = random.choice(self.STATE_SLOTS)
        self._episode_slot = slot
        s = self._read_state()
        tries = 0
        while not self._match_ready(s, provisional=True):
            mode = tries % 3
            if mode == 0:
                self._send(f"loadstate {slot}")
                time.sleep(2.5)
            elif mode == 1 and pydirectinput is not None:
                pydirectinput.press(self.LOAD_STATE_KEY)
                time.sleep(2.5)
            else:
                self._send("press 4 6")
                time.sleep(1.5)
            s = self._read_state()
            tries += 1
            if tries % 15 == 0:
                print(f"[env] still trying to restart the match ({tries} tries)")
        if tries:
            print(f"[env] match ready after {tries} tries")
        if not s["v4"] and not self._warned_v3:
            print("[env] WARNING: v3 state line — slot has no playerbase "
                  "calibration; facing/3rd-4th opponents zeroed. See "
                  "RUN3_CALIBRATION.md")
            self._warned_v3 = True
        i = self.AGENT_PLAYER - 1
        # opponents active THIS EPISODE: healthy at reset AND (v3: player 0
        # only) (v4: any player with a pinned matrix = nonzero pos)
        self._active_opp = []
        for j in range(4):
            if j == i:
                continue
            pinned = s["v4"] and np.any(s["players"][j]["pos"] != 0)
            if (not s["v4"] and j == 0) or pinned:
                if s["h"][j] > 100.0:
                    self._active_opp.append(j)
        if not self._active_opp:
            self._active_opp = [0 if i != 0 else 1]
        self.baseline = [max(h, 1000.0) for h in s["h"]]
        self.prev = s
        self.prev_health = self._frac(s["h"])
        self.last_action = 0
        self._form_timer = 0
        self._ep = self._fresh_ep()
        self._stone_tracks = []
        self._my_g_int = 0
        self._opp_g_int = 0
        self._hurt_cd = 0
        self._dealt_cd = 0
        return self._observe(s, s)

    def step(self, action):
        name, mask = self.ACTIONS[action]
        start = self._read_state()
        self._send(f"press {mask} {self.ACTION_FRAMES}")
        s = self._wait_frames(start["frame"], self.ACTION_FRAMES)
        self.steps += 1

        health = self._frac(s["h"])
        reward, done, info = self._reward(health, s)
        obs = self._observe(s, self.prev)
        self.prev = s
        self.prev_health = health
        self.last_action = action

        if self.steps >= self.MAX_STEPS:
            done, info["timeout"] = True, True
        if done:
            e = self._ep
            res = info.get("result", "timeout")
            slot = getattr(self, "_episode_slot", 0)
            nopp = len(self._active_opp)
            print(f"[ep] slot{slot} opps={nopp} {res:>7} len={self.steps:4d}  "
                  f"dmg {e['dmg_out']:+.2f}/{e['dmg_in']:+.2f}  "
                  f"stones: picked={e['picks']} lost={e['lost']} "
                  f"opp={e['opicks']}(-{e['oploose']}) "
                  f"forms={e['forms']}/{e['oforms']}  "
                  f"gem_rew={e['gem']:+.2f} shape={e['stone']:+.2f} "
                  f"appr={e['approach']:+.2f}")
            try:
                with open(os.path.join(BRIDGE_DIR, "ep_stats_v4.csv"), "a") as f:
                    f.write(f"{s['frame']},{res},{self.steps},"
                            f"{e['dmg_out']:.3f},{e['dmg_in']:.3f},"
                            f"{e['approach']:.3f},{e['gem']:.3f},"
                            f"{e['stone']:.3f},{e['picks']},{e['lost']},"
                            f"{e['opicks']},{e['oploose']},"
                            f"{e['forms']},{e['oforms']},{slot},{nopp}\n")
            except OSError:
                pass
        return obs, reward, done, info

    def render(self, mode="human"):
        pass

    def close(self):
        if self.TURBO_KEY and pydirectinput is not None:
            pydirectinput.keyUp(self.TURBO_KEY)

    # -------------------------------------------------------------- internals

    @staticmethod
    def _fresh_ep():
        return {"dmg_out": 0.0, "dmg_in": 0.0, "approach": 0.0, "gem": 0.0,
                "stone": 0.0, "picks": 0, "lost": 0, "opicks": 0,
                "oploose": 0, "forms": 0, "oforms": 0}

    def _frac(self, raw):
        return [min(1.5, r / b) for r, b in zip(raw, self.baseline)]

    def _alive(self, h):
        return h > 0.001

    def _match_ready(self, s, provisional=False):
        i = self.AGENT_PLAYER - 1
        me = s["h"][i]
        if provisional:
            opps = [h for j, h in enumerate(s["h"]) if j != i]
            return me > 100.0 and any(h > 100.0 for h in opps)
        return me > 100.0 and any(s["h"][j] > 100.0 for j in self._active_opp)

    def _me(self, s):
        return s["players"][self.AGENT_PLAYER - 1]

    def _opps(self, s, alive_only=True):
        """Active opponents as (player_idx, player_dict), nearest first."""
        me = self._me(s)["pos"]
        health = self._frac(s["h"])
        out = []
        for j in self._active_opp:
            if alive_only and not self._alive(health[j]):
                continue
            p = s["players"][j]
            d = float(np.hypot(p["pos"][0] - me[0], p["pos"][2] - me[2]))
            out.append((d, j, p))
        out.sort(key=lambda t: t[0])
        return out

    def _dist_nearest(self, s):
        opps = self._opps(s)
        if not opps:
            opps = self._opps(s, alive_only=False)
        return opps[0][0] if opps else 0.0

    def _nearest_opp_xz(self, s):
        opps = self._opps(s)
        if not opps:
            opps = self._opps(s, alive_only=False)
        if not opps:
            return None
        p = opps[0][2]["pos"]
        return p[0], p[2]

    def _counter_gems(self, s):
        """Gem system v3 (Aug 3 night): LEDGER-based — real per-player
        logic-object counters from the v5 line. No proximity inference,
        no phantoms possible. Falls back to _stone_gems when unanchored."""
        i = self.AGENT_PLAYER - 1
        me_n, me_p = s["players"][i], self.prev["players"][i]
        gem, e = 0.0, self._ep
        d = me_n["gems"] - me_p["gems"]
        formed = me_n["form"] == 1 and me_p["form"] == 0
        if formed:
            gem += self.TRANSFORM_BONUS
            e["forms"] += 1
            self._form_timer = self.FORM_STEPS
        if me_n["form"] == 1:
            self._form_timer = max(self._form_timer, 2)
        if d > 0:
            gem += self.GEM_W * d
            e["picks"] += d
        elif d < 0 and not formed and me_p["form"] != 1:
            # real knock-loss only: the HUD byte holds 3 THROUGH a form and
            # drops 3->0 when it ENDS (me_p form==1 guards that), and the
            # transform-start consumption is guarded by `formed`
            gem -= self.LOST_W * (-d)
            e["lost"] += -d
        self._my_g_int = max(0, min(3, me_n["gems"]))
        opp_max = 0
        for _dst, j, pn in self._opps(s):
            pp = self.prev["players"][j]
            if pn.get("gems", -1) < 0 or pp.get("gems", -1) < 0:
                continue
            od = pn["gems"] - pp["gems"]
            oformed = pn["form"] == 1 and pp["form"] == 0
            if oformed:
                gem -= self.OPP_TRANSFORM_PEN
                e["oforms"] += 1
            if od > 0:
                gem -= self.OPP_GEM_W * od
                e["opicks"] += od
            elif od < 0 and not oformed and self._dealt_cd > 0:
                gem += self.KNOCK_W * (-od)
                e["oploose"] += -od
            opp_max = max(opp_max, min(3, pn["gems"]))
        self._opp_g_int = opp_max
        if self._opp_g_int >= 2:
            gem -= self.DANGER_W
        return gem

    def _counters_ok(self, s):
        i = self.AGENT_PLAYER - 1
        return (s.get("v5") and self.prev.get("v5")
                and s["players"][i].get("gems", -1) >= 0
                and self.prev["players"][i].get("gems", -1) >= 0)

    def _stone_gems(self, s):
        """Gem system v2, multi-opponent: attribution vs NEAREST opponent."""
        pme = self._me(self.prev)["pos"]
        # attribution = who is closest to the STONE, over ALL active
        # opponents — the old nearest-to-bot single opponent over-credited
        # the bot for COM pickups in 4P scrums
        opps_xz = [(p["pos"][0], p["pos"][2])
                   for _d, _j, p in self._opps(self.prev)]
        raw = list(s.get("stones") or [])
        cur = []   # dedupe: cluster entities -> one stone
        for x, z in raw:
            if not any(abs(x - cx) <= self.STONE_DEDUPE_TOL
                       and abs(z - cz) <= self.STONE_DEDUPE_TOL
                       for cx, cz in cur):
                cur.append((x, z))
        gem, e = 0.0, self._ep

        used = [False] * len(cur)
        new_tracks = []
        for tr in self._stone_tracks:
            hit = None
            for k, (x, z) in enumerate(cur):
                if (not used[k] and abs(x - tr["x"]) <= self.STONE_MATCH_TOL
                        and abs(z - tr["z"]) <= self.STONE_MATCH_TOL):
                    hit = k
                    break
            if hit is not None:
                used[hit] = True
                new_tracks.append({"x": tr["x"], "z": tr["z"],
                                   "age": tr["age"] + 1})
                continue
            if tr["age"] < self.STONE_MIN_AGE:
                continue
            dme = ((tr["x"] - pme[0]) ** 2 + (tr["z"] - pme[2]) ** 2) ** 0.5
            dopp = float("inf")
            for ox, oz in opps_xz:
                d = ((tr["x"] - ox) ** 2 + (tr["z"] - oz) ** 2) ** 0.5
                if d < dopp:
                    dopp = d
            if min(dme, dopp) > self.PICKUP_R:
                continue
            if dme < self.PICKUP_MARGIN * dopp and dme <= self.PICKUP_R_ME:
                gem += self.GEM_W
                e["picks"] += 1
                self._my_g_int += 1
                if self._my_g_int >= 3:
                    gem += self.TRANSFORM_BONUS
                    e["forms"] += 1
                    self._my_g_int = 0
                    self._form_timer = self.FORM_STEPS
            elif dopp < self.PICKUP_MARGIN * dme:
                gem -= self.OPP_GEM_W
                e["opicks"] += 1
                self._opp_g_int += 1
                if self._opp_g_int >= 3:
                    gem -= self.OPP_TRANSFORM_PEN
                    e["oforms"] += 1
                    self._opp_g_int = 0

        for k, (x, z) in enumerate(cur):
            if used[k]:
                continue
            dme = ((x - pme[0]) ** 2 + (z - pme[2]) ** 2) ** 0.5
            dopp = float("inf")
            for ox, oz in opps_xz:
                d = ((x - ox) ** 2 + (z - oz) ** 2) ** 0.5
                if d < dopp:
                    dopp = d
            if (dme <= dopp and dme <= self.KNOCK_R and self._my_g_int > 0
                    and self._hurt_cd > 0):
                self._my_g_int -= 1
                gem -= self.LOST_W
                e["lost"] += 1
            elif (dopp < dme and dopp <= self.KNOCK_R and self._opp_g_int > 0
                    and self._dealt_cd > 0):
                self._opp_g_int -= 1
                gem += self.KNOCK_W
                e["oploose"] += 1
            new_tracks.append({"x": x, "z": z, "age": 1})

        self._stone_tracks = new_tracks
        if self._opp_g_int >= 2:
            gem -= self.DANGER_W
        return gem

    def _observe(self, s, prev):
        i = self.AGENT_PLAYER - 1
        health = self._frac(s["h"])
        me, pme = self._me(s), self._me(prev)
        mp, pp = me["pos"], pme["pos"]

        obs = np.zeros(self.OBS_DIM, dtype=np.float32)
        # ---- self
        obs[0] = health[i]
        obs[1] = mp[0] / POS_SCALE
        obs[2] = mp[2] / POS_SCALE
        obs[3] = mp[1] / HEIGHT_SCALE
        obs[4] = (mp[0] - pp[0]) / VEL_SCALE
        obs[5] = (mp[2] - pp[2]) / VEL_SCALE
        obs[6], obs[7] = me["face"][0], me["face"][1]
        g_real = me.get("gems", -1)
        obs[8] = (min(3, g_real) / 3.0) if g_real >= 0 else self._my_g_int / 3.0
        obs[9] = (1.0 if me.get("form", 0) == 1
                  else self._form_timer / float(self.FORM_STEPS))
        # ---- opponents, nearest first
        prev_pos = {j: prev["players"][j]["pos"] for j in self._active_opp}
        for k, (d, j, p) in enumerate(self._opps(s)[:self.N_OPP]):
            b = self._OPP0 + 9 * k
            op, of = p["pos"], p["face"]
            obs[b + 0] = 1.0
            obs[b + 1] = (op[0] - mp[0]) / POS_SCALE
            obs[b + 2] = (op[2] - mp[2]) / POS_SCALE
            obs[b + 3] = (op[1] - mp[1]) / HEIGHT_SCALE
            obs[b + 4] = d / POS_SCALE
            pv = prev_pos.get(j, op)
            obs[b + 5] = (op[0] - pv[0]) / VEL_SCALE
            obs[b + 6] = (op[2] - pv[2]) / VEL_SCALE
            obs[b + 7] = health[j]
            # threat-dot: their facing vs the unit vector from them to me
            to_me = np.array([mp[0] - op[0], mp[2] - op[2]])
            n = np.linalg.norm(to_me)
            if n > 1.0 and (of[0] != 0 or of[1] != 0):
                obs[b + 8] = float(np.dot(of, to_me / n))
        # ---- stones (all pool pairs, present flags)
        for k, (x, z) in enumerate((s.get("stones") or [])[:4]):
            b = self._STN0 + 3 * k
            obs[b] = 1.0
            obs[b + 1] = (x - mp[0]) / POS_SCALE
            obs[b + 2] = (z - mp[2]) / POS_SCALE
        # ---- stage one-hot + last action (spares stay zero)
        slot = getattr(self, "_episode_slot", 0)
        if 0 <= slot < 4:
            obs[self._STG0 + slot] = 1.0
        obs[self._ACT0 + self.last_action] = 1.0
        return np.clip(obs, -5.0, 5.0)

    def _reward(self, health, s):
        i = self.AGENT_PLAYER - 1
        me_now, me_prev = health[i], self.prev_health[i]
        pairs = [(self.prev_health[j], health[j]) for j in self._active_opp
                 if self._alive(self.prev_health[j]) or self._alive(health[j])]

        damage_dealt = sum(max(0.0, p - n) for p, n in pairs)
        own_delta = me_now - me_prev
        approach = (self._dist_nearest(self.prev)
                    - self._dist_nearest(s)) / POS_SCALE

        if own_delta < 0:
            self._hurt_cd = 5
        elif self._hurt_cd:
            self._hurt_cd -= 1
        if damage_dealt > 0:
            self._dealt_cd = 5
        elif self._dealt_cd:
            self._dealt_cd -= 1
        gem = (self._counter_gems(s) if self._counters_ok(s)
               else self._stone_gems(s))
        # hard clamp: cumulative episode gem reward stays inside
        # [-GEM_EP_CAP, +GEM_EP_CAP] — Aug 2's "nothing outbids a win"
        # rule, now structural (slot-0 phantom storms hit +67/ep without it)
        _g0 = self._ep["gem"]
        gem = max(-self.GEM_EP_CAP - _g0, min(gem, self.GEM_EP_CAP - _g0))

        stone_shape = 0.0
        if self._form_timer > 0:
            self._form_timer -= 1
        else:
            cur, prv = s.get("stones") or [], self.prev.get("stones") or []
            if cur and prv:
                pos_now = self._me(s)["pos"]
                pos_prev = self._me(self.prev)["pos"]
                mx, mz = pos_now[0], pos_now[2]
                sx, sz = min(cur, key=lambda p: (p[0] - mx) ** 2
                                                + (p[1] - mz) ** 2)
                if any(abs(px - sx) < 2.0 and abs(pz - sz) < 2.0
                       for px, pz in prv):
                    d_now = ((sx - mx) ** 2 + (sz - mz) ** 2) ** 0.5
                    d_prev = ((sx - pos_prev[0]) ** 2
                              + (sz - pos_prev[2]) ** 2) ** 0.5
                    stone_shape = ((d_prev - d_now) / POS_SCALE
                                   * self.STONE_APPROACH_W)

        e = self._ep
        e["dmg_out"] += self.DAMAGE_DEALT_W * damage_dealt
        e["dmg_in"] += self.DAMAGE_TAKEN_W * min(0.0, own_delta)
        e["approach"] += self.APPROACH_W * approach
        e["gem"] += gem
        e["stone"] += stone_shape

        reward = (self.DAMAGE_DEALT_W * damage_dealt
                  + self.DAMAGE_TAKEN_W * min(0.0, own_delta)
                  + self.APPROACH_W * approach
                  + gem
                  + stone_shape
                  - self.TIME_PENALTY)

        done, info = False, {"health": health, "dist": self._dist_nearest(s)}
        if not self._alive(me_now) and self._alive(me_prev):
            reward -= self.LOSS_PENALTY
            done, info["result"] = True, "loss"
        elif pairs and all(not self._alive(n) for _, n in pairs):
            reward += self.WIN_BONUS
            done, info["result"] = True, "win"
        return reward, done, info

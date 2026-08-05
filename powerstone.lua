-- powerstone.lua — Flycast Lua bridge for Power Stone 2 RL training.
--
-- Enable in Flycast: Settings > Advanced > Lua Scripting -> powerstone.lua
-- (file must sit in the Flycast folder, next to emu.cfg). Restart Flycast.
--
-- FIRST RUN — hands-free address finder:
--   1. Start a match. Click [AUTO find] once.
--   2. Play: beat up ONE player only (or let a COM do it). No clicking needed.
--      The script watches RAM and eliminates everything that isn't health.
--   3. When 1 finalist remains it locks P1 automatically (2-4: pause and
--      click the right one — the bar that snaps down instantly on a hit;
--      a slow-draining bar is the display's fake trailing bar).
--   4. Then let the OTHER player take one hit — P2 locks automatically,
--      addresses save, bridge starts. Done forever.
--
-- EVERY RUN AFTER: bridge auto-starts. Streams health to ps2_state.txt,
-- executes commands (button presses, savestate load) from ps2_cmd.txt.

-- ======================== configuration ========================
-- ============================= CONFIG =============================
-- EDIT THIS: absolute path of the bridge/ directory this repo creates,
-- WITH trailing backslashes (Windows) or slash (Linux). The Python env
-- and this script communicate entirely through files in this directory.
local DIR = "C:\\path\\to\\PowerStone2AI_RL\\bridge\\"
-- ==================================================================
local STATE_FILE = DIR .. "ps2_state.txt"
local CMD_FILE   = DIR .. "ps2_cmd.txt"
local ADDR_FILE  = DIR .. "ps2_addr.txt"

local RAM_BASES  = { 0x8C000000, 0x0C000000, 0xAC000000 }
local RAM_SIZE   = 0x01000000        -- 16 MB
local SCAN_CHUNK = 65536             -- u32s per frame (bulk mode)
local SAMPLE_EVERY = 20              -- frames between auto-narrow passes

-- plausible health floats, as raw bit patterns (positive floats sort like
-- their bit patterns): 50.0 .. 10000.0 for the scan, >=1.0 for damaged values
local RANGE_LO, RANGE_HI, ONE = 0x42480000, 0x461C4000, 0x3F800000

-- ======================== state ========================
local addrs = {}
local byte_keys = {}                 -- keys read with read8 (addr@off syntax)
local frame = 0
-- Per-player press state: press[player] = { mask, left }. press_player is
-- the default target for the legacy 'press' command; 'pressp' addresses any
-- player explicitly (self-play: frozen model drives P1 while P2 trains).
local press = {}
local press_player = 2
-- Command-file protocol v2: Python prefixes every command with an
-- incrementing sequence number ("<seq>|<cmd>"). We remember the last seq we
-- executed and skip the file while it still shows that seq — the file is
-- NEVER deleted, which kills the delete-after-read ENOENT race that fumbled
-- the first gem recording. last_seq is echoed at the end of every state
-- line as the ack. Lines without a seq prefix (hand-dropped via device_bash)
-- keep the old delete-after-read behavior.
local last_seq = 0
-- Savestate ops must NOT run inside the vblank (emulator-thread) callback —
-- that deadlocks the emulator. They're queued here and executed from the
-- overlay (UI-thread) callback instead, like the menu button would.
local pending_state = nil

-- Full-RAM snapshot machinery (ramdump command) — for state-diff RE:
-- dump untransformed / transformed / untransformed, diff offline.
local ramdump = nil

-- Memory-layout recorder: periodically dumps the regions around the known
-- player structs to a log for offline analysis (position/gem field hunting).
local rec = nil
local REC_REGIONS = {
  { 0x8C474000, 0x4000 },   -- health cluster neighborhood (player status)
  { 0x8C531000, 0x3000 },   -- the secondary copy region
}
local REC_EVERY = 30          -- frames between snapshots (~0.5 s)
local REC_FRAMES = 3600       -- stop after ~60 s

-- Position-source scanner: sweeps RAM for addresses that mirror the live
-- position values of the render buffer, to locate the game's logic structs.
local posscan = nil
local POS_SRC_X, POS_SRC_Z = 0x8C532958, 0x8C532960

-- Loose-stone / entity scanner (Tier 2 item hunt): sweeps RAM for float
-- x/?/z triples (x at a, y at a+4, z at a+8 — same layout as the player
-- structs) whose x and z land near a target coordinate. Drive it remotely:
--   fscan <cx> <cz> <r>        full-RAM sweep, hits -> ps2_fscan.txt
--   ffilter still              keep hits whose x AND z are bit-identical
--                              to when they were captured (static stones)
--   ffilter moved              keep hits whose x or z changed
--   ffilter near <cx> <cz> <r> keep hits currently within a new box
-- Every command rewrites ps2_fscan.txt ("ADDR,x,z" lines, count header).
local fscan = nil            -- active sweep: { pos, cx, cz, r }
local fhits = nil            -- results: array of { a, xb, zb } (bit patterns)
local FSCAN_FILE = DIR .. "ps2_fscan.txt"
local FSCAN_MAX_HITS = 20000

local cand_addr, cand_val = nil, nil
local scan_pos, scan_total, base_idx = nil, nil, 1
local scan_base_addr, scan_words, full_scan = nil, nil, true
local use_bulk = false  -- Aug 3: readTable32 returns garbage WITHOUT erroring in this build -> matscan 0-hits + the posscan "zero copies" dead end. Never re-enable.
local prev_addr, prev_val = nil, nil
local auto = false
local stats = nil            -- parallel arrays: drops/incs/chg + samples
local last_sample = 0
local status_msg = nil

-- ======================== helpers ========================
local function bitsToFloat(b)
  if b == 0 then return 0.0 end
  local sign = 1
  if b >= 0x80000000 then sign = -1; b = b - 0x80000000 end
  local exp = math.floor(b / 0x800000) % 256
  local mant = b % 0x800000
  if exp == 0 then return sign * (mant / 0x800000) * 2.0 ^ (-126) end
  return sign * (1.0 + mant / 0x800000) * 2.0 ^ (exp - 127)
end

local function saneDrop(cur, prev)
  if cur >= prev then return false end
  return cur == 0 or (cur >= ONE and cur <= RANGE_HI)
end

local function loadAddrs()
  local f = io.open(ADDR_FILE, "r")
  if not f then return false end
  addrs, byte_keys = {}, {}
  for line in f:lines() do
    -- "k=0xADDR@N" -> single BYTE at ADDR+N (read8); plain "k=0xADDR" -> u32
    local k, v, off = line:match("^(%w+)=(0[xX]%x+)@(%d+)$")
    if k then
      addrs[k] = tonumber(v) + tonumber(off)
      byte_keys[k] = true
    else
      k, v = line:match("^(%w+)=(%w+)$")
      if k and v then addrs[k] = tonumber(v) end
    end
  end
  f:close()
  return addrs.p1 ~= nil and addrs.p2 ~= nil
end

local function saveAddrs()
  local f = io.open(ADDR_FILE, "w")
  for _, k in ipairs({"p1", "p2", "p3", "p4"}) do
    if addrs[k] then f:write(string.format("%s=0x%08X\n", k, addrs[k])) end
  end
  f:close()
end

local function health(k)
  if not addrs[k] then return 0.0 end
  local v = bitsToFloat(flycast.memory.read32(addrs[k]))
  if v ~= v or v < 0 or v > 20000 then return 0.0 end
  return v
end

local bridge_ok = loadAddrs()

-- ======================== bridge ========================
local function readF(k)
  if not addrs[k] then return 0.0 end
  local v = bitsToFloat(flycast.memory.read32(addrs[k]))
  if v ~= v or v < -100000 or v > 100000 then return 0.0 end
  return v
end

local function readI(k)
  if not addrs[k] then return 0 end
  local v
  if byte_keys[k] then
    v = flycast.memory.read8(addrs[k])
  else
    v = flycast.memory.read32(addrs[k])
  end
  if v > 100 then v = 0 end
  return v
end

-- Loose power stones live in the ENTITY POOL (pinned Aug 1 2026 via fscan +
-- three recordings, see HANDOFF): slots 0-3 of the 0x90-stride pool at
-- 0x8C3EE000 are the stone slots. A slot holds a grabbable loose stone iff
-- active(+0x34)==1 AND its instance pointer(+0x3C) is in the stones' arena
-- band (chests are 0x0C5Dxxxx, ground items 0x0C61xxxx, sub-second effect
-- junk 0x0C58xxxx). Position floats sit at +0x8C/+0x90/+0x94 (x/y/z, y~50
-- hover). Picked-up stones DEACTIVATE the slot, so no carried-stone ghosts.
-- WIDENED Aug 2 2026 (Blake: "are we sure it can actually see them?"):
-- stones knocked OUT of a player respawn in pool slots BEYOND 0-3 —
-- invisible to the obs, the approach shaping, AND the pickup detector.
-- Telemetry smoking gun: knock-out events (oploose) read ~0 across ~800
-- episodes while Blake watched the bot knock stones loose constantly.
-- Fix: scan the whole pool (slots 0..STONE_SCAN_SLOTS-1) for active
-- entities in the stone class band, report the first 4 found. The state
-- line keeps its 4-pair format — env and model need NO changes.
local STONE_POOL_DEFAULT, STONE_SLOTS_DEFAULT = 0x8C3EE000, 64
local STONE_POOL, STONE_STRIDE = STONE_POOL_DEFAULT, 0x90
local STONE_CLS_LO_DEFAULT, STONE_CLS_HI_DEFAULT = 0x0C591000, 0x0C593000
-- Aug 3: 4P VS mode uses DIFFERENT loose-stone classes (dropped stones =
-- 0x0C596E60; the 0x0C591xxx entities there are invisible always-active
-- spawn-pad records = phantoms). Band is now PER-SLOT via ps2_pool.txt.
local STONE_CLS_LO, STONE_CLS_HI = STONE_CLS_LO_DEFAULT, STONE_CLS_HI_DEFAULT
local STONE_SCAN_SLOTS = STONE_SLOTS_DEFAULT   -- full-pool coverage
-- PERF (Aug 2): the 64-slot sweep every vblank in slow-read mode cost
-- ~30% fps (18 -> 13). Stones are static, and the env only samples every
-- 6 frames — so sweep every STONE_SCAN_EVERY vblanks and serve a cached
-- line fragment between sweeps. Same visibility, a third of the reads.
local STONE_SCAN_EVERY = 3
local stone_sp_cache = "0.00,0.00,0.00,0.00,0.00,0.00,0.00,0.00"
local stone_scan_last = -10

-- PER-STAGE POOL BASES (Aug 2 2026): the entity pool does NOT live at
-- 0x8C3EE000 on every stage — submarine and tomb were STONE-BLIND because
-- the sweep looked at the desert stage's base. Fix, zero env/model changes:
--   stonescan                 full-RAM sweep for the stone SIGNATURE
--                             (active==1 @+0x34, class ptr in the stone
--                             band @+0x3C, sane floats @+0x8C/+0x94),
--                             hits -> ps2_stonescan.txt with a suggested
--                             stonebase line. Run it while loose stones
--                             are ON SCREEN on the stage in question.
--   stonebase <slot> <0xADDR> [nslots]
--                             pin a savestate slot's pool base (persisted
--                             to ps2_pool.txt, survives restarts).
-- Every 'loadstate <n>' now applies pool_map[n] (or the default) — the env
-- already loads a state per episode, so per-stage bases ride for free.
-- KEY INSIGHT for interpreting stonescan hits: all slots of one pool share
-- the same residue mod 0x90, so ANY window with a hit's residue that spans
-- the hits works as a base — we don't need the true slot-0 address.
local STONESCAN_FILE = DIR .. "ps2_stonescan.txt"
local POOL_FILE = DIR .. "ps2_pool.txt"
local STONESCAN_MAX = 512
local stonescan = nil        -- active sweep: { pos }
local shits = nil            -- results: array of { b, cls, x, y, z }
local cur_slot = nil         -- last loadstate slot (Lua idx), nil at boot
local pool_map = {}          -- [slot] = { base, slots }

local function loadPool()
  pool_map = {}
  local f = io.open(POOL_FILE, "r")
  if not f then return end
  for line in f:lines() do
    local s, b, n, cl, ch =
      line:match("^(%d+)=(0[xX]%x+),(%d+),(0[xX]%x+),(0[xX]%x+)$")
    if not s then
      s, b, n = line:match("^(%d+)=(0[xX]%x+),(%d+)$")
    end
    if s then
      pool_map[tonumber(s)] = { base = tonumber(b), slots = tonumber(n),
        clo = cl and tonumber(cl), chi = ch and tonumber(ch) }
    end
  end
  f:close()
end

local function savePool()
  local f = io.open(POOL_FILE, "w")
  if not f then return end
  for s, e in pairs(pool_map) do
    if e.clo then
      f:write(string.format("%d=0x%08X,%d,0x%08X,0x%08X\n",
        s, e.base, e.slots, e.clo, e.chi))
    else
      f:write(string.format("%d=0x%08X,%d\n", s, e.base, e.slots))
    end
  end
  f:close()
end

local function applyPoolFor(slot)
  local e = pool_map[slot]
  if e then
    STONE_POOL, STONE_SCAN_SLOTS = e.base, e.slots
    STONE_CLS_LO = e.clo or STONE_CLS_LO_DEFAULT
    STONE_CLS_HI = e.chi or STONE_CLS_HI_DEFAULT
  else
    STONE_POOL, STONE_SCAN_SLOTS = STONE_POOL_DEFAULT, STONE_SLOTS_DEFAULT
    STONE_CLS_LO, STONE_CLS_HI = STONE_CLS_LO_DEFAULT, STONE_CLS_HI_DEFAULT
  end
  stone_scan_last = -10   -- next writeState re-sweeps immediately
end

loadPool()

-- PER-SLOT PLAYER RENDER MATRICES (run-3 / v4 obs, Aug 3 2026): each
-- player's known "position" addr is the translation column (+0x30) of a
-- 4x4 render matrix; rotation row 0 (+0x00,+0x08) is the facing unit
-- vector and +0x34 the REAL height. Matrix bases are heap-allocated per
-- match, but savestates freeze allocation -> calibrate ONCE per savestate
-- slot (matscan) and persist here. When the current slot has pinned
-- bases, writeState emits the v4 line (36 fields); otherwise the v3 line
-- (22) — a running v3 env is never disturbed.
--   playerbase <slot> <m1> <m2> <m3> <m4>   (0 = player absent/unknown)
--   matscan                                  full-RAM 4x4-matrix sweep,
--     two passes; hits + moved-flag -> ps2_matscan.txt (players move,
--     furniture doesn't — in a live match COMs never stand still long)
local PLAYERS_FILE = DIR .. "ps2_players.txt"
local MATSCAN_FILE = DIR .. "ps2_matscan.txt"
local MATSCAN_MAX = 16384  -- Aug 3: 4096 capped at 0x8C3EF000, never reached players; filter below is also much tighter now
local players_map = {}       -- [slot] = { m1, m2, m3, m4 }
local matscan = nil          -- active sweep: { pos, pass }
local mhits = nil            -- { { b, x, y, z, x2, z2 } }

local function loadPlayers()
  players_map = {}
  local f = io.open(PLAYERS_FILE, "r")
  if not f then return end
  for line in f:lines() do
    local s, m1, m2, m3, m4 =
      line:match("^(%d+)=(0[xX]%x+),(0[xX]%x+),(0[xX]%x+),(0[xX]%x+)$")
    if s then
      players_map[tonumber(s)] = { tonumber(m1), tonumber(m2),
                                   tonumber(m3), tonumber(m4) }
    end
  end
  f:close()
end

local function savePlayers()
  local f = io.open(PLAYERS_FILE, "w")
  if not f then return end
  for s, m in pairs(players_map) do
    f:write(string.format("%d=0x%08X,0x%08X,0x%08X,0x%08X\n",
      s, m[1], m[2], m[3], m[4]))
  end
  f:close()
end

loadPlayers()

-- PER-SLOT PLAYER LOGIC OBJECTS (gem counters + form flags, Aug 3 night):
-- F = the form-flag word of each player's heap logic object (see
-- RE_FINDINGS_AUG3.md). F+0x1B8 = REAL gem count (mirror +0x1BC),
-- F+0x00 = 0x00010000 while transformed. Heap-allocated per match but
-- frozen by savestates -> anchor per slot: `gembase <slot> <F1..F4>`
-- (0 = unanchored), persisted in ps2_gems.txt. When the current slot has
-- anchors, writeState emits the v5 line (44 fields).
local GEMS_FILE = DIR .. "ps2_gems.txt"
local gems_map = {}   -- [slot] = { {c,f} x4 }: c = COUNT word addr,
                      -- f = FORM-FLAG word addr (0x00010000 = formed).
                      -- Two structure types exist (near-skeleton block:
                      -- flag at count-0x1AC; heap logic object: flag at
                      -- count-0x1B8) -> store BOTH addrs explicitly.
local watch_addrs = {}  -- overlay live-memory watches (watch <a1> ...)

local function loadGems()
  gems_map = {}
  local f = io.open(GEMS_FILE, "r")
  if not f then return end
  for line in f:lines() do
    local t = {}
    local slot = line:match("^(%d+)=")
    for hexv in line:gmatch("(0[xX]%x+)") do t[#t + 1] = tonumber(hexv) end
    if slot and #t == 8 then
      gems_map[tonumber(slot)] = { {t[1], t[2]}, {t[3], t[4]},
                                   {t[5], t[6]}, {t[7], t[8]} }
    end
  end
  f:close()
end

local function saveGems()
  local f = io.open(GEMS_FILE, "w")
  if not f then return end
  for s, g in pairs(gems_map) do
    f:write(string.format(
      "%d=0x%08X,0x%08X,0x%08X,0x%08X,0x%08X,0x%08X,0x%08X,0x%08X\n",
      s, g[1][1], g[1][2], g[2][1], g[2][2],
      g[3][1], g[3][2], g[4][1], g[4][2]))
  end
  f:close()
end

loadGems()

local function stoneXZ(k)
  local b = STONE_POOL + k * STONE_STRIDE
  if flycast.memory.read32(b + 0x34) ~= 1 then return 0.0, 0.0 end
  local cls = flycast.memory.read32(b + 0x3C)
  if cls < STONE_CLS_LO or cls >= STONE_CLS_HI then return 0.0, 0.0 end
  local x = bitsToFloat(flycast.memory.read32(b + 0x8C))
  local z = bitsToFloat(flycast.memory.read32(b + 0x94))
  if x ~= x or z ~= z or x < -100000 or x > 100000
     or z < -100000 or z > 100000 then return 0.0, 0.0 end
  return x, z
end

local function writeState()
  local f = io.open(STATE_FILE, "w")
  if not f then return end
  if addrs.p1x and addrs.p2x then
    -- extended line v3 (22 fields): frame,h1..h4,p1x,p1y,p1z,p2x,p2y,p2z,
    -- g1,g2,s0x,s0z,s1x,s1z,s2x,s2z,s3x,s3z,cmdseq. Inactive/non-stone
    -- slots report 0,0. cmdseq must stay the LAST field (it's the command
    -- ack) — the python side reads it from the end of the line.
    -- Full-pool stone sweep (see STONE_SCAN_SLOTS note above): first 4
    -- active stone-band entities anywhere in the pool, compacted to the
    -- front; remaining pairs pad with 0,0. The env treats the 4 pairs as
    -- an unordered set, so compaction is safe. Swept every
    -- STONE_SCAN_EVERY vblanks; cached fragment served between sweeps.
    if frame - stone_scan_last >= STONE_SCAN_EVERY then
      stone_scan_last = frame
      local sp = {}
      for k = 0, STONE_SCAN_SLOTS - 1 do
        if #sp >= 4 then break end
        local ok, x, z = pcall(stoneXZ, k)
        if ok and (x ~= 0.0 or z ~= 0.0) then
          sp[#sp + 1] = string.format("%.2f,%.2f", x, z)
        end
      end
      while #sp < 4 do sp[#sp + 1] = "0.00,0.00" end
      stone_sp_cache = table.concat(sp, ",")
    end
    local pm = cur_slot and players_map[cur_slot]
    if pm then
      -- v4 line (36 fields): frame,h1..h4,[x,y,z,fx,fz]x4,g1,g2,
      -- s0x..s3z,cmdseq. Positions/facing from the per-slot 4x4 render
      -- matrices (translation +0x30/34/38, facing row +0x00/+0x08).
      -- Unpinned players (base 0) report zeros; env gates on that.
      local blocks = {}
      for k = 1, 4 do
        local b = pm[k]
        if b and b ~= 0 then
          local ok, s5 = pcall(function()
            local read32 = flycast.memory.read32
            return string.format("%.2f,%.2f,%.2f,%.4f,%.4f",
              bitsToFloat(read32(b + 0x30)), bitsToFloat(read32(b + 0x34)),
              bitsToFloat(read32(b + 0x38)), bitsToFloat(read32(b + 0x00)),
              bitsToFloat(read32(b + 0x08)))
          end)
          blocks[k] = ok and s5 or "0,0,0,0,0"
        else
          blocks[k] = "0,0,0,0,0"
        end
      end
      local gm = cur_slot and gems_map[cur_slot]
      if gm then
        -- v5 line (44 fields): v4's 35 + G1..G4 (real gem counts, -1 =
        -- unanchored/bad read) + f1..f4 (form flags 0/1), cmdseq LAST.
        local gparts, fparts = {}, {}
        for k = 1, 4 do
          local pair = gm[k]
          local ca, fa = pair[1], pair[2]
          if ca and ca ~= 0 then
            local ok, gv, fv = pcall(function()
              -- count = a BYTE address (HUD gauge byte, e.g. F+0x35);
              -- flag = the object's F word, formed bit = 0x00010000
              -- (base bits vary per player type -> BIT test, not equality)
              local g = flycast.memory.read8(ca)
              local fw = (fa and fa ~= 0) and flycast.memory.read32(fa) or 0
              local formed = (math.floor(fw / 0x10000) % 2 == 1) and 1 or 0
              return g, formed
            end)
            if ok and gv >= 0 and gv <= 99 then
              gparts[k], fparts[k] = tostring(gv), tostring(fv)
            else
              gparts[k], fparts[k] = "-1", "0"
            end
          else
            gparts[k], fparts[k] = "-1", "0"
          end
        end
        f:write(string.format(
          "%d,%.2f,%.2f,%.2f,%.2f,%s,%s,%s,%s,%d,%d,%s,%s,%s,%s,%s,%s,%s,%s,%s,%d\n",
          frame, health("p1"), health("p2"), health("p3"), health("p4"),
          blocks[1], blocks[2], blocks[3], blocks[4],
          readI("g1"), readI("g2"), stone_sp_cache,
          gparts[1], gparts[2], gparts[3], gparts[4],
          fparts[1], fparts[2], fparts[3], fparts[4], last_seq))
      else
        f:write(string.format(
          "%d,%.2f,%.2f,%.2f,%.2f,%s,%s,%s,%s,%d,%d,%s,%d\n",
          frame, health("p1"), health("p2"), health("p3"), health("p4"),
          blocks[1], blocks[2], blocks[3], blocks[4],
          readI("g1"), readI("g2"), stone_sp_cache, last_seq))
      end
    else
      f:write(string.format(
        "%d,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%d,%d,%s,%d\n",
        frame, health("p1"), health("p2"), health("p3"), health("p4"),
        readF("p1x"), readF("p1y"), readF("p1z"),
        readF("p2x"), readF("p2y"), readF("p2z"),
        readI("g1"), readI("g2"), stone_sp_cache, last_seq))
    end
  else
    f:write(string.format("%d,%.2f,%.2f,%.2f,%.2f,%d\n", frame,
            health("p1"), health("p2"), health("p3"), health("p4"), last_seq))
  end
  f:close()
end

local function applyPress(pl, mask, frames)
  local p = press[pl]
  if p and p.mask ~= 0 then flycast.input.releaseButtons(pl, p.mask) end
  press[pl] = { mask = mask, left = frames }
  if mask ~= 0 then flycast.input.pressButtons(pl, mask) end
end

-- fscan result writer + filter live above runCommand on purpose: runCommand
-- calls them, and a later 'local function' would be an invisible nil global
-- from up here (Lua upvalues only see locals declared BEFORE the closure).
local function fscanWrite()
  local f = io.open(FSCAN_FILE, "w")
  if not f then return end
  f:write(string.format("# %d hits\n", fhits and #fhits or 0))
  if fhits then
    for _, h in ipairs(fhits) do
      f:write(string.format("%08X,%.2f,%.2f\n",
        h.a, bitsToFloat(h.xb), bitsToFloat(h.zb)))
    end
  end
  f:close()
end

local function fscanFilter(pred)
  if not fhits then return end
  local keep = {}
  for _, h in ipairs(fhits) do
    local xb = flycast.memory.read32(h.a)
    local zb = flycast.memory.read32(h.a + 8)
    if pred(h, xb, zb) then
      keep[#keep + 1] = { a = h.a, xb = xb, zb = zb }
    end
  end
  fhits = keep
  fscanWrite()
end

local function matscanWrite()
  local f = io.open(MATSCAN_FILE, "w")
  if not f then
    local ef = io.open(DIR .. "ps2_matscan_err.txt", "w")
    if ef then ef:write("matscanWrite: io.open failed for " .. MATSCAN_FILE); ef:close() end
    return
  end
  f:write(string.format("# matscan %d hits, slot=%s (moved=1 -> live actor)\n",
    #mhits, tostring(cur_slot)))
  f:write("# base,x,y,z,moved\n")
  for _, h in ipairs(mhits) do
    local moved = 0
    if math.abs(h.x2 - h.x) > 0.5 or math.abs(h.z2 - h.z) > 0.5 then
      moved = 1
    end
    f:write(string.format("%08X,%.2f,%.2f,%.2f,%d\n",
      h.b, h.x, h.y, h.z, moved))
  end
  f:close()
end

local readChunk  -- FORWARD DECL (Aug 3): real def is in the scanning section below; without this, matscanStep called a nil GLOBAL readChunk -> silent pcall abort, 0 hits, no file
local function matscanStep()
  -- pass 1: sweep RAM for the 4x4 render-matrix signature — 0x3F800000 at
  -- +0x3C from a base whose translation (+0x30/+0x34/+0x38) is sane.
  -- pass 2 (after a ~1 s gap): re-read each hit's translation; movers are
  -- live actors (players, projectiles), statics are stage furniture.
  if matscan.pass == 2 then
    if frame < matscan.resume_at then return end
    local read32 = flycast.memory.read32
    for _, h in ipairs(mhits) do
      h.x2 = bitsToFloat(read32(h.b + 0x30))
      h.z2 = bitsToFloat(read32(h.b + 0x38))
    end
    matscan = nil
    matscanWrite()
    return
  end
  local scan_end = matscan.endpos or (RAM_SIZE / 4)
  local base = 0x8C000000 + matscan.pos * 4
  local count = math.min(2048, scan_end - matscan.pos)
  local t = readChunk(base, count)
  local read32 = flycast.memory.read32
  for i = 1, count do
    if t[i] == 0x3F800000 then
      local b = base + (i - 1) * 4 - 0x3C
      if b >= 0x8C000000 and b + 0x3C < 0x8C000000 + RAM_SIZE
         -- Aug 3 tightened signature: a real 4x4 has right column (0,0,0,1)
         and read32(b + 0x0C) == 0 and read32(b + 0x1C) == 0
         and read32(b + 0x2C) == 0 then
        local r0x = bitsToFloat(read32(b + 0x00))
        local r0z = bitsToFloat(read32(b + 0x08))
        local x = bitsToFloat(read32(b + 0x30))
        local y = bitsToFloat(read32(b + 0x34))
        local z = bitsToFloat(read32(b + 0x38))
        if x == x and y == y and z == z and r0x == r0x and r0z == r0z
           and r0x >= -1.01 and r0x <= 1.01 and r0z >= -1.01 and r0z <= 1.01
           and x > -32000 and x < 32000 and z > -32000 and z < 32000
           and y > -5000 and y < 10000
           and not (x == 0 and z == 0) then
          mhits[#mhits + 1] = { b = b, x = x, y = y, z = z, x2 = x, z2 = z }
        end
      end
    end
  end
  matscan.pos = matscan.pos + count
  if #mhits >= MATSCAN_MAX or matscan.pos >= scan_end then
    matscan.pass = 2
    matscan.resume_at = frame + 60     -- let actors move for ~1 s
  end
end

local function stonescanWrite()
  local f = io.open(STONESCAN_FILE, "w")
  if not f then return end
  local lo, hi
  for _, h in ipairs(shits) do
    if not lo or h.b < lo then lo = h.b end
    if not hi or h.b > hi then hi = h.b end
  end
  f:write(string.format("# stonescan %d hits, slot=%s, active pool=0x%08X\n",
    #shits, tostring(cur_slot), STONE_POOL))
  if lo then
    -- All slots of a pool share lo's residue mod the stride, so a window
    -- anchored a few slots below the lowest hit is guaranteed to cover
    -- every hit (see the per-stage pool notes up top). Margin below covers
    -- slots that happened to be empty during this scan.
    local margin = 8
    local sbase = lo - margin * STONE_STRIDE
    if sbase < 0x8C000000 then sbase = lo end
    local nslots = math.floor((hi - sbase) / STONE_STRIDE) + margin + 1
    if nslots < STONE_SLOTS_DEFAULT then nslots = STONE_SLOTS_DEFAULT end
    f:write(string.format("# suggest: stonebase %s 0x%08X %d\n",
      tostring(cur_slot or 0), sbase, nslots))
  end
  for _, h in ipairs(shits) do
    f:write(string.format("%08X,%08X,%.2f,%.2f,%.2f\n",
      h.b, h.cls, h.x, h.y, h.z))
  end
  f:close()
end

local function runCommand(line)
  -- self-play: one write sets BOTH players' buttons (P1 = frozen model,
  -- P2 = live bot) — half the bridge round-trips of two pressp commands
  local m1, m2, bframes = line:match("^pressboth (%d+) (%d+) (%d+)$")
  if m1 then
    applyPress(1, tonumber(m1), tonumber(bframes))
    applyPress(2, tonumber(m2), tonumber(bframes))
    return
  end
  local pl, mask, frames = line:match("^pressp (%d+) (%d+) (%d+)$")
  if pl then
    applyPress(tonumber(pl), tonumber(mask), tonumber(frames))
    return
  end
  mask, frames = line:match("^press (%d+) (%d+)$")
  if mask then
    applyPress(press_player, tonumber(mask), tonumber(frames))
    return
  end
  local slot = line:match("^loadstate (%d+)$")
  if slot then
    slot = tonumber(slot)
    cur_slot = slot
    applyPoolFor(slot)         -- per-stage pool base rides every reset
    pending_state = { op = "load", slot = slot }
    return
  end
  slot = line:match("^savestate (%d+)$")
  if slot then pending_state = { op = "save", slot = tonumber(slot) }; return end
  local pl = line:match("^player (%d+)$")
  if pl then press_player = tonumber(pl); return end
  -- remote research commands
  -- record <secs> <base> <len> [...]          snapshot every 30 frames (legacy)
  -- record <secs> x<every> <base> <len> [...] snapshot every <every> frames
  -- (RE mega-take uses x10 = 6 snaps/s; dump rows carry the vblank frame
  -- number, which the overlay also displays — that pairing is what lets a
  -- screen recording be aligned to dump rows frame-by-frame.)
  local secs, every, rest = line:match("^record (%d+) x(%d+) (.+)$")
  if not secs then
    secs, rest = line:match("^record (%d+) (.+)$")
    every = nil
  end
  if secs then
    local regions = {}
    for b, l in rest:gmatch("(0[xX]%x+) (0[xX]%x+)") do
      regions[#regions + 1] = { tonumber(b), tonumber(l) }
    end
    if #regions > 0 then
      REC_REGIONS = regions
      REC_EVERY = (every and tonumber(every)) or 30
      if REC_EVERY < 1 then REC_EVERY = 1 end
      REC_FRAMES = tonumber(secs) * 60
      local f = io.open(DIR .. "ps2_dump.txt", "w")
      if f then
        -- self-describing header so the offline analyzer needs no side info
        local rparts = {}
        for _, r in ipairs(regions) do
          rparts[#rparts + 1] = string.format("%08X:%X", r[1], r[2])
        end
        f:write(string.format("H,secs=%s,every=%d,startframe=%d,regions=%s\n",
          secs, REC_EVERY, frame, table.concat(rparts, "+")))
        rec = { file = f, t0 = frame }
      end
    end
    return
  end
  if line == "recstop" then
    if rec then rec.file:close(); rec = nil end
    return
  end
  -- ramdump <id> [words_per_vblank]: FULL 16MB RAM snapshot, smeared over
  -- ~RAM/(wpv*4) vblanks (default 32768 wpv → ~128 vblanks ≈ 2-4 s wall).
  -- Only meaningful while the game state of interest is HELD CONSTANT
  -- (subject stands still / stays transformed). Written to
  -- ps2_ramdump_<id>.txt with start/end frame stamps for the video sync.
  local did, dwpv = line:match("^ramdump (%w+) (%d+)$")
  if not did then did = line:match("^ramdump (%w+)$") end
  if did then
    if ramdump then pcall(function() ramdump.file:close() end) end
    local f = io.open(DIR .. "ps2_ramdump_" .. did .. ".txt", "w")
    if f then
      f:write(string.format("D,start,%d\n", frame))
      ramdump = { file = f, pos = 0,
                  wpv = (dwpv and tonumber(dwpv)) or 32768 }
    end
    return
  end
  if line == "reloadaddrs" then
    bridge_ok = loadAddrs()
    return
  end
  if line == "posscan" then
    posscan = { pos = 0, sweep = 1, hits = {} }
    return
  end
  if line == "stonescan" then
    shits = {}
    stonescan = { pos = 0, clo = STONE_CLS_LO, chi = STONE_CLS_HI }
    return
  end
  -- band override for stages that relocate the stone class code:
  -- stonescan 0x0C580000 0x0C600000 (wide sweep, review hits by class col)
  local clo, chi = line:match("^stonescan (0[xX]%x+) (0[xX]%x+)$")
  if clo then
    shits = {}
    stonescan = { pos = 0, clo = tonumber(clo), chi = tonumber(chi) }
    return
  end
  if line == "matscan" then
    mhits = {}
    matscan = { pos = 0, pass = 1 }
    return
  end
  local mlo, mhi = line:match("^matscan (0[xX]%x+) (0[xX]%x+)$")
  if mlo then
    -- Aug 3: windowed variant, e.g. `matscan 0x8C400000 0x8C700000` (~6 s)
    mlo, mhi = tonumber(mlo), tonumber(mhi)
    if mlo and mhi and mhi > mlo and mlo >= 0x8C000000
       and mhi <= 0x8C000000 + RAM_SIZE then
      mhits = {}
      matscan = { pos = math.floor((mlo - 0x8C000000) / 4), pass = 1,
                  endpos = math.floor((mhi - 0x8C000000) / 4) }
    end
    return
  end
  local ps, pm1, pm2, pm3, pm4 =
    line:match("^playerbase (%d+) (0[xX]?%x*) (0[xX]?%x*) (0[xX]?%x*) (0[xX]?%x*)$")
  if ps then
    players_map[tonumber(ps)] = { tonumber(pm1) or 0, tonumber(pm2) or 0,
                                  tonumber(pm3) or 0, tonumber(pm4) or 0 }
    savePlayers()
    return
  end
  local gs = line:match("^gembase (%d+)")
  if gs then
    local t = {}
    for hexv in line:gmatch("(0[xX]%x+)") do t[#t + 1] = tonumber(hexv) end
    -- accepts: gembase <slot> <c1> <f1> <c2> <f2> <c3> <f3> <c4> <f4>
    if #t == 8 then
      gems_map[tonumber(gs)] = { {t[1], t[2]}, {t[3], t[4]},
                                 {t[5], t[6]}, {t[7], t[8]} }
      saveGems()
    end
    return
  end
  if line:match("^watch ") then
    watch_addrs = {}
    for hexv in line:gmatch("(0[xX]%x+)") do
      if #watch_addrs < 8 then watch_addrs[#watch_addrs + 1] = tonumber(hexv) end
    end
    return
  end
  if line == "watchclear" then watch_addrs = {}; return end
  local sslot, sbase, sn, sclo, schi = line:match(
    "^stonebase (%d+) (0[xX]%x+) (%d+) (0[xX]%x+) (0[xX]%x+)$")
  if not sslot then
    sslot, sbase, sn = line:match("^stonebase (%d+) (0[xX]%x+) (%d+)$")
  end
  if not sslot then
    sslot, sbase = line:match("^stonebase (%d+) (0[xX]%x+)$")
  end
  if sslot then
    sslot = tonumber(sslot)
    pool_map[sslot] = { base = tonumber(sbase),
                        slots = (sn and tonumber(sn)) or STONE_SLOTS_DEFAULT,
                        clo = sclo and tonumber(sclo),
                        chi = schi and tonumber(schi) }
    savePool()
    if sslot == cur_slot then applyPoolFor(cur_slot) end
    return
  end
  local cx, cz, r = line:match("^fscan (%-?[%d%.]+) (%-?[%d%.]+) ([%d%.]+)$")
  if cx then
    fhits = {}
    fscan = { pos = 0, cx = tonumber(cx), cz = tonumber(cz), r = tonumber(r) }
    return
  end
  if line == "ffilter still" then
    fscanFilter(function(h, xb, zb) return xb == h.xb and zb == h.zb end)
    return
  end
  if line == "ffilter moved" then
    fscanFilter(function(h, xb, zb) return xb ~= h.xb or zb ~= h.zb end)
    return
  end
  cx, cz, r = line:match("^ffilter near (%-?[%d%.]+) (%-?[%d%.]+) ([%d%.]+)$")
  if cx then
    cx, cz, r = tonumber(cx), tonumber(cz), tonumber(r)
    fscanFilter(function(h, xb, zb)
      local x, z = bitsToFloat(xb), bitsToFloat(zb)
      return x == x and z == z
        and x >= cx - r and x <= cx + r and z >= cz - r and z <= cz + r
    end)
    return
  end
end

local function pollCommands()
  local f = io.open(CMD_FILE, "r")
  if not f then return end
  local line = f:read("*l")
  f:close()
  if not line then return end
  local seq, rest = line:match("^(%d+)|(.+)$")
  if seq then
    seq = tonumber(seq)
    if seq == last_seq then return end   -- already executed; leave file alone
    last_seq = seq
    runCommand(rest)
    return
  end
  -- legacy line without a seq prefix (manual drop): consume-and-delete
  os.remove(CMD_FILE)
  runCommand(line)
end

local function tickPress()
  for pl, p in pairs(press) do
    if p.left > 0 then
      p.left = p.left - 1
      if p.left == 0 and p.mask ~= 0 then
        flycast.input.releaseButtons(pl, p.mask)
        p.mask = 0
      end
    end
  end
end

-- ======================== scanning ========================
function readChunk(addr, count)  -- assigns the forward-declared local above
  if use_bulk then
    local ok, t = pcall(flycast.memory.readTable32, addr, count)
    if ok and type(t) == "table" and (t[1] ~= nil or t[0] ~= nil) then
      local off = (t[0] ~= nil) and 0 or 1
      local out = {}
      for i = 1, count do out[i] = t[i - 1 + off] end
      return out
    end
    use_bulk = false
  end
  local out = {}
  local read32 = flycast.memory.read32
  for i = 1, count do out[i] = read32(addr + (i - 1) * 4) end
  return out
end

local function posscanStep()
  local vx = flycast.memory.read32(POS_SRC_X)
  local vz = flycast.memory.read32(POS_SRC_Z)
  local base = 0x8C000000 + posscan.pos * 4
  local count = math.min(2048, RAM_SIZE / 4 - posscan.pos)
  local t = readChunk(base, count)
  for i = 1, count do
    if t[i] == vx then
      local a = base + (i - 1) * 4
      if a + 8 < 0x8C000000 + RAM_SIZE
         and flycast.memory.read32(a + 8) == vz then
        posscan.hits[a] = (posscan.hits[a] or 0) + 1
      end
    end
  end
  posscan.pos = posscan.pos + count
  if posscan.pos >= RAM_SIZE / 4 then
    posscan.pos = 0
    posscan.sweep = posscan.sweep + 1
    if posscan.sweep > 3 then
      local f = io.open(DIR .. "ps2_posscan.txt", "w")
      if f then
        for a, h in pairs(posscan.hits) do
          f:write(string.format("%08X,%d\n", a, h))
        end
        f:close()
      end
      posscan = nil
    end
  end
end

local function fscanStep()
  local base = 0x8C000000 + fscan.pos * 4
  local count = math.min(2048, RAM_SIZE / 4 - fscan.pos)
  local t = readChunk(base, count)
  for i = 1, count do
    local xb = t[i]
    if xb and xb ~= 0 then
      local x = bitsToFloat(xb)
      if x == x and x >= fscan.cx - fscan.r and x <= fscan.cx + fscan.r then
        local a = base + (i - 1) * 4
        if a + 8 < 0x8C000000 + RAM_SIZE then
          local zb = flycast.memory.read32(a + 8)
          local z = bitsToFloat(zb)
          if z == z and z >= fscan.cz - fscan.r and z <= fscan.cz + fscan.r then
            local y = bitsToFloat(flycast.memory.read32(a + 4))
            if y == y and y > -5000 and y < 5000 then
              fhits[#fhits + 1] = { a = a, xb = xb, zb = zb }
            end
          end
        end
      end
    end
  end
  fscan.pos = fscan.pos + count
  if #fhits > FSCAN_MAX_HITS or fscan.pos >= RAM_SIZE / 4 then
    fscan = nil
    fscanWrite()
  end
end

local function stonescanStep()
  -- Hunt the stone signature anywhere in RAM: a word in the stone class
  -- band is treated as a candidate +0x3C instance pointer; the entity base
  -- is word_addr-0x3C and must show active==1 @+0x34 and sane position
  -- floats @+0x8C/+0x90/+0x94. Budget: <=2048 words/vblank, like fscan.
  local base = 0x8C000000 + stonescan.pos * 4
  local count = math.min(2048, RAM_SIZE / 4 - stonescan.pos)
  local t = readChunk(base, count)
  local read32 = flycast.memory.read32
  for i = 1, count do
    local cls = t[i]
    if cls and cls >= stonescan.clo and cls < stonescan.chi then
      local b = base + (i - 1) * 4 - 0x3C
      if b >= 0x8C000000 and b + 0x94 < 0x8C000000 + RAM_SIZE
         and read32(b + 0x34) == 1 then
        local x = bitsToFloat(read32(b + 0x8C))
        local y = bitsToFloat(read32(b + 0x90))
        local z = bitsToFloat(read32(b + 0x94))
        if x == x and y == y and z == z
           and x > -100000 and x < 100000
           and z > -100000 and z < 100000
           and y > -5000 and y < 5000 then
          shits[#shits + 1] = { b = b, cls = cls, x = x, y = y, z = z }
        end
      end
    end
  end
  stonescan.pos = stonescan.pos + count
  if #shits >= STONESCAN_MAX or stonescan.pos >= RAM_SIZE / 4 then
    stonescan = nil
    stonescanWrite()
  end
end

local function ramdumpStep()
  local base = 0x8C000000 + ramdump.pos * 4
  local count = math.min(ramdump.wpv, RAM_SIZE / 4 - ramdump.pos)
  local pos = 0
  while pos < count do
    local n = math.min(8192, count - pos)
    local t = readChunk(base + pos * 4, n)
    local parts = {}
    for i = 1, n do parts[i] = string.format("%08X", t[i] or 0) end
    ramdump.file:write(string.format("R,%08X,", base + pos * 4))
    ramdump.file:write(table.concat(parts))
    ramdump.file:write("\n")
    pos = pos + n
  end
  ramdump.pos = ramdump.pos + count
  if ramdump.pos >= RAM_SIZE / 4 then
    ramdump.file:write(string.format("D,end,%d\n", frame))
    ramdump.file:close()
    ramdump = nil
  end
end

local function recordSnapshot()
  local cur = press[press_player]
  rec.file:write(string.format("S,%d,%d\n", frame, (cur and cur.mask) or 0))
  for _, r in ipairs(REC_REGIONS) do
    local base, len = r[1], r[2]
    local words = len / 4
    local pos = 0
    while pos < words do
      local n = math.min(8192, words - pos)
      local t = readChunk(base + pos * 4, n)
      local parts = {}
      for i = 1, n do parts[i] = string.format("%08X", t[i] or 0) end
      rec.file:write(string.format("R,%08X,", base + pos * 4))
      rec.file:write(table.concat(parts))
      rec.file:write("\n")
      pos = pos + n
    end
  end
  rec.file:flush()   -- survive a mid-take emulator crash
end

local function startScan(base, autoflag)
  base_idx = base or 1
  scan_base_addr = RAM_BASES[base_idx]
  scan_words = RAM_SIZE / 4
  full_scan = true
  cand_addr, cand_val = {}, {}
  prev_addr, prev_val, stats, scan_total = nil, nil, nil, nil
  scan_pos = 0
  if autoflag ~= nil then auto = autoflag end
  addrs.p1, addrs.p2 = nil, nil
  status_msg = nil
end

local function startRegionScan(center, radius)
  -- Targeted scan around a known address (player structs are neighbors).
  local ram_lo, ram_hi = RAM_BASES[1], RAM_BASES[1] + RAM_SIZE
  local lo = math.max(ram_lo, center - radius)
  local hi = math.min(ram_hi, center + radius)
  lo = lo - (lo % 4)
  scan_base_addr = lo
  scan_words = math.floor((hi - lo) / 4)
  full_scan = false
  cand_addr, cand_val = {}, {}
  prev_addr, prev_val, stats, scan_total = nil, nil, nil, nil
  scan_pos = 0
  auto = false
  status_msg = "Region scan: filter, hit P2, filter, tag."
end

local function scanStep()
  local count = math.min(use_bulk and SCAN_CHUNK or 8192, scan_words - scan_pos)
  local t = readChunk(scan_base_addr + scan_pos * 4, count)
  for i = 1, count do
    local v = t[i]
    if v ~= nil and v >= RANGE_LO and v <= RANGE_HI then
      cand_addr[#cand_addr + 1] = scan_base_addr + (scan_pos + i - 1) * 4
      cand_val[#cand_val + 1] = v
    end
  end
  scan_pos = scan_pos + count
  if scan_pos >= scan_words then
    scan_pos = nil
    scan_total = #cand_addr
    if scan_total == 0 and full_scan and base_idx < #RAM_BASES then
      startScan(base_idx + 1, nil)  -- wrong RAM mirror; try the next
    end
  end
end

-- ======================== manual filters ========================
local function filterKeep(pred)
  prev_addr, prev_val = cand_addr, cand_val
  stats = nil
  local ka, kv = {}, {}
  for i = 1, #cand_addr do
    local cur = flycast.memory.read32(cand_addr[i])
    if pred(cur, cand_val[i]) then
      ka[#ka + 1] = cand_addr[i]; kv[#kv + 1] = cur
    end
  end
  cand_addr, cand_val = ka, kv
end

local function undoFilter()
  if prev_addr then
    cand_addr, cand_val = prev_addr, prev_val
    prev_addr, prev_val, stats = nil, nil, nil
  end
end

local function resync()
  -- Re-snapshot every candidate WITHOUT filtering. Click after any rematch /
  -- round change: values that legitimately reset (like health refilling)
  -- would otherwise look like eliminations to the filters.
  for i = 1, #cand_addr do
    cand_val[i] = flycast.memory.read32(cand_addr[i])
  end
  stats = nil
end

-- ======================== auto-narrow ========================
local function initStats()
  stats = { drops = {}, incs = {}, chg = {}, samples = 0 }
  for i = 1, #cand_addr do
    stats.drops[i], stats.incs[i], stats.chg[i] = 0, 0, 0
  end
end

local function compact(keep)
  -- keep[i] boolean -> rebuild all parallel arrays
  local ka, kv, kd, ki, kc = {}, {}, {}, {}, {}
  for i = 1, #cand_addr do
    if keep[i] then
      ka[#ka + 1] = cand_addr[i]; kv[#kv + 1] = cand_val[i]
      kd[#kd + 1] = stats.drops[i]; ki[#ki + 1] = stats.incs[i]
      kc[#kc + 1] = stats.chg[i]
    end
  end
  cand_addr, cand_val = ka, kv
  stats.drops, stats.incs, stats.chg = kd, ki, kc
end

local function finalists()
  local out = {}
  if not stats then return out end
  for i = 1, #cand_addr do
    if stats.drops[i] >= 1 and stats.incs[i] == 0
       and stats.chg[i] * 2 < stats.samples then
      out[#out + 1] = i
    end
  end
  return out
end

local function autoPass()
  if stats == nil or #stats.drops ~= #cand_addr then initStats() end
  local dropped_now = {}
  for i = 1, #cand_addr do
    local cur = flycast.memory.read32(cand_addr[i])
    if cur ~= cand_val[i] then
      stats.chg[i] = stats.chg[i] + 1
      if saneDrop(cur, cand_val[i]) then
        if stats.drops[i] == 0 then
          dropped_now[#dropped_now + 1] = { i = i, prev = cand_val[i] }
        end
        stats.drops[i] = stats.drops[i] + 1
      else
        stats.incs[i] = stats.incs[i] + 1
      end
      cand_val[i] = cur
    end
  end
  stats.samples = stats.samples + 1

  -- prune obvious non-health: ever-increased, or churning like a timer
  if stats.samples >= 8 and stats.samples % 4 == 0 then
    local keep = {}
    for i = 1, #cand_addr do
      keep[i] = stats.incs[i] == 0
                and not (stats.chg[i] >= stats.samples * 0.8)
    end
    compact(keep)
  end

  -- phase 1 -> auto-lock P1 when exactly one clean dropper has emerged
  if not addrs.p1 then
    local f = finalists()
    if #f == 1 and stats.samples >= 12 then
      addrs.p1 = cand_addr[f[1]]
      status_msg = string.format("P1 locked: 0x%08X", addrs.p1)
    end
  elseif not addrs.p2 then
    -- phase 2: first fresh dropper (that isn't P1) = P2. Guards against
    -- physics junk: (a) long stabilization first, (b) candidate must have
    -- been quiet until now, (c) its PRE-hit value must be a clean float —
    -- full health is 1000.0/2000.0-style (low mantissa bits all zero),
    -- drifting world values virtually never are.
    if stats.samples < 15 then return end
    local best, bestdist = nil, nil
    for _, d in ipairs(dropped_now) do
      local i = d.i
      if cand_addr[i] ~= addrs.p1 and stats.chg[i] <= 2
         and d.prev % 0x10000 == 0 then
        local dist = math.abs(cand_addr[i] - addrs.p1)
        if bestdist == nil or dist < bestdist then best, bestdist = i, dist end
      end
    end
    if best then
      addrs.p2 = cand_addr[best]
      saveAddrs()
      bridge_ok = true
      auto = false
      status_msg = string.format("P2 locked: 0x%08X — BRIDGE ON", addrs.p2)
    end
  end
end

-- ======================== UI ========================
local function candidateRows(indices)
  for _, i in ipairs(indices) do
    local a = cand_addr[i]
    local v = bitsToFloat(flycast.memory.read32(a))
    flycast.ui.text(string.format("0x%08X %8.1f", a, v))
    flycast.ui.bargraph(math.max(0, math.min(1, v / 1000.0)))
    flycast.ui.button(string.format("This is P1 (%04X)", a % 0x10000),
      function() addrs.p1 = a; status_msg = nil end)
    flycast.ui.button(string.format("This is P2 (%04X)", a % 0x10000),
      function() addrs.p2 = a end)
  end
end

local function overlay()
  if pending_state then
    local op = pending_state
    pending_state = nil
    if op.op == "load" then
      pcall(flycast.emulator.loadState, op.slot)
    else
      pcall(flycast.emulator.saveState, op.slot)
    end
  end
  flycast.ui.beginWindow("PS2 RL", 10, 10, 360, 0)
  if bridge_ok then
    flycast.ui.text(string.format("BRIDGE ACTIVE  frame %d", frame))
    for _, k in ipairs({"p1", "p2", "p3", "p4"}) do
      if addrs[k] then
        flycast.ui.text(string.format("%s %8.1f", k:upper(), health(k)))
        flycast.ui.bargraph(math.min(1, health(k) / 1000.0))
      end
    end
    if posscan then
      flycast.ui.text(string.format("posscan sweep %d/3: %d%%",
        posscan.sweep, math.floor(posscan.pos * 400 / RAM_SIZE)))
    end
    if fscan then
      flycast.ui.text(string.format("fscan %d%% (%d hits)",
        math.floor(fscan.pos * 400 / RAM_SIZE), #fhits))
    elseif fhits then
      flycast.ui.text(string.format("fscan: %d hits -> ps2_fscan.txt", #fhits))
    end
    if stonescan then
      flycast.ui.text(string.format("stonescan %d%% (%d hits)",
        math.floor(stonescan.pos * 400 / RAM_SIZE), #shits))
    elseif shits then
      flycast.ui.text(string.format("stonescan: %d hits -> ps2_stonescan.txt",
        #shits))
    end
    flycast.ui.text(string.format("pool 0x%08X x%d slot %s",
      STONE_POOL, STONE_SCAN_SLOTS, cur_slot and tostring(cur_slot) or "-"))
    if matscan then
      flycast.ui.text(string.format("matscan pass %d: %d%% (%d hits)",
        matscan.pass, math.floor(matscan.pos * 400 / RAM_SIZE), #mhits))
    elseif mhits then
      flycast.ui.text(string.format("matscan: %d hits -> ps2_matscan.txt",
        #mhits))
    end
    for _, wa in ipairs(watch_addrs) do
      local okw, wv = pcall(flycast.memory.read32, wa)
      if okw then
        flycast.ui.text(string.format("W %08X: %08X (%d | %.2f)",
          wa, wv, wv, bitsToFloat(wv)))
      end
    end
    local pmv = cur_slot and players_map[cur_slot]
    local gmv = cur_slot and gems_map[cur_slot]
    flycast.ui.text(string.format("state line: %s",
      (pmv and gmv) and "v5 (players+gems)"
      or (pmv and "v4 (players pinned)" or "v3")))
    if ramdump then
      flycast.ui.text(string.format("RAMDUMP %d%%",
        math.floor(ramdump.pos * 400 / RAM_SIZE)))
    end
    if rec then
      flycast.ui.text(string.format("REC %ds / %ds",
        math.floor((frame - rec.t0) / 60), math.floor(REC_FRAMES / 60)))
    else
      flycast.ui.button("Record memory layout (60s)", function()
        local f = io.open(DIR .. "ps2_dump.txt", "w")
        if f then rec = { file = f, t0 = frame } end
      end)
    end
    flycast.ui.button("Rescan addresses", function()
      bridge_ok = false; addrs = {}; cand_addr = nil; auto = false
    end)
  elseif scan_pos ~= nil then
    flycast.ui.text(string.format("Scanning 0x%08X... %d%%%s",
      scan_base_addr, math.floor(scan_pos * 100 / scan_words),
      use_bulk and "" or " (slow mode)"))
  elseif cand_addr == nil then
    flycast.ui.text("Start a match first, then:")
    if addrs.p1 and not addrs.p2 then
      flycast.ui.text(string.format("P1 known: 0x%08X", addrs.p1))
      flycast.ui.button("Find P2 NEAR P1 (best)", function()
        local keep = addrs.p1
        startRegionScan(keep, 0x40000)
        addrs.p1 = keep
      end)
    end
    flycast.ui.button("AUTO find (recommended)", function() startScan(1, true) end)
    flycast.ui.button("Manual scan", function() startScan(1, false) end)
  else
    if scan_total then
      flycast.ui.text(string.format("scan found %d (base 0x%08X)%s",
        scan_total, RAM_BASES[base_idx], use_bulk and "" or " slow-mode"))
    end
    flycast.ui.text(string.format("%d candidates", #cand_addr))
    if status_msg then flycast.ui.text(status_msg) end

    if auto then
      if not addrs.p1 then
        local f = finalists()
        flycast.ui.text("AUTO: beat up ONE player only...")
        flycast.ui.text(string.format("clean droppers: %d", #f))
        if #f >= 1 and #f <= 4 then
          flycast.ui.text("(fake trailing bars drain slowly —")
          flycast.ui.text(" the real one snaps down instantly)")
          candidateRows(f)
        end
      elseif not addrs.p2 then
        flycast.ui.text(string.format("P1 = 0x%08X", addrs.p1))
        if stats == nil or stats.samples < 15 then
          flycast.ui.text(string.format("stabilizing... %d/15 — DON'T hit anyone",
            stats and stats.samples or 0))
        else
          flycast.ui.text(">>> NOW: hit P2 once, at FULL health <<<")
          flycast.ui.text("(nobody else takes damage)")
        end
      end
      flycast.ui.button("Re-sync (click after a rematch)", resync)
      flycast.ui.button("Stop auto / manual mode", function() auto = false end)
    else
      flycast.ui.text("Manual narrowing:")
      flycast.ui.button("Someone JUST took a hit",
        function() filterKeep(saneDrop) end)
      flycast.ui.button("Nobody hit for a while",
        function() filterKeep(function(c, p) return c == p end) end)
      flycast.ui.button("Re-sync (click after a rematch)", resync)
      if prev_addr then flycast.ui.button("Undo last filter", undoFilter) end
      if #cand_addr > 0 and #cand_addr <= 10 then
        local all = {}
        for i = 1, #cand_addr do all[#all + 1] = i end
        candidateRows(all)
      end
      flycast.ui.button("Switch to AUTO", function()
        auto = true; stats = nil
      end)
    end
    if addrs.p1 and not addrs.p2 then
      flycast.ui.button("Find P2 NEAR P1 (best)", function()
        local keep = addrs.p1
        startRegionScan(keep, 0x40000)
        addrs.p1 = keep
      end)
    end
    flycast.ui.button("Restart scan", function() startScan(1, auto) end)

    if addrs.p1 then flycast.ui.text(string.format("P1 -> 0x%08X", addrs.p1)) end
    if addrs.p2 then flycast.ui.text(string.format("P2 -> 0x%08X", addrs.p2)) end
    if addrs.p1 and addrs.p2 and addrs.p1 ~= addrs.p2 and not bridge_ok then
      flycast.ui.button("SAVE + start bridge", function()
        saveAddrs(); bridge_ok = true
      end)
    end
  end
  flycast.ui.endWindow()
end

-- ======================== callbacks ========================
flycast_callbacks = {
  vblank = function()
    frame = frame + 1
    if scan_pos ~= nil then scanStep() end
    if auto and scan_pos == nil and cand_addr ~= nil and not bridge_ok
       and frame - last_sample >= SAMPLE_EVERY then
      last_sample = frame
      autoPass()
    end
    if bridge_ok then
      tickPress()
      pollCommands()
      writeState()
      if posscan then
        local ok = pcall(posscanStep)
        if not ok then posscan = nil end  -- abort scan, never kill the bridge
      end
      if fscan then
        local ok = pcall(fscanStep)
        if not ok then fscan = nil end   -- abort scan, never kill the bridge
      end
      if stonescan then
        local ok = pcall(stonescanStep)
        if not ok then stonescan = nil end  -- abort scan, never kill the bridge
      end
      if matscan then
        local ok, mserr = pcall(matscanStep)
        if not ok then
          matscan = nil                      -- abort scan, never kill the bridge
          local ef = io.open(DIR .. "ps2_matscan_err.txt", "w")
          if ef then ef:write(tostring(mserr)); ef:close() end
        end
      end
      if ramdump then
        local ok = pcall(ramdumpStep)
        if not ok then
          pcall(function() ramdump.file:close() end)
          ramdump = nil            -- abort dump, never kill the bridge
        end
      end
      if rec then
        if (frame - rec.t0) % REC_EVERY == 0 then
          local ok = pcall(recordSnapshot)
          if not ok then          -- disk/IO hiccup: stop the recording,
            pcall(function() rec.file:close() end)
            rec = nil             -- never kill the bridge
          end
        end
        if rec and frame - rec.t0 >= REC_FRAMES then
          rec.file:close()
          rec = nil
        end
      end
    end
  end,
  overlay = overlay,
}

-- Plant power (cm-viz's "power" scene) as a one-line diagram you can walk:
-- sources on the north side, the main switchboard (MSB) across the middle,
-- branch buses and their loads to the south, the genset, ATS and EP-1 in the
-- south-east. Conductors are copper and turn grey when their bus is dead;
-- chevrons ride each conductor in the direction of the flow.
local P = {}
cm.power = P
local O = { x = 70, y = 0, z = 0 }
local function at(x, z, y) return { x = O.x + x, y = O.y + (y or 1), z = O.z + z } end
P.region = { { x = O.x - 16, y = -4, z = O.z - 24 }, { x = O.x + 52, y = 24, z = O.z + 24 } }
P.origin = O

local BRANCH_X = { mcc1 = -8, lp1 = -2, boiler = 4, pkg = 10, rtu = 16, msb = 24 }
local segs = {} -- name -> { a, b, bus, nodes = {...} }
local labels, loads_drawn = {}, {}

-- A chevron rides one conductor; speed follows the flow, direction its sign.
core.register_entity("cm_world:chevron", {
  initial_properties = { visual = "cube", visual_size = { x = 0.5, y = 0.5, z = 0.5 },
    textures = { "default_mese_block.png", "default_mese_block.png", "default_mese_block.png", "default_mese_block.png", "default_mese_block.png", "default_mese_block.png" },
    glow = 12, physical = false, pointable = false, static_save = false },
  on_step = function(self, dtime)
    local s = self.seg
    if not s then return end
    if not s.flowing then self.object:set_properties({ is_visible = false }); return end
    self.object:set_properties({ is_visible = true })
    self.phase = ((self.phase or self.offset or 0) + dtime * s.speed * s.dir / math.max(1, s.len)) % 1
    local t = self.phase
    self.object:set_pos({ x = s.a.x + (s.b.x - s.a.x) * t, y = s.a.y + 0.9, z = s.a.z + (s.b.z - s.a.z) * t })
  end,
})

local function seg(name, a, b, bus)
  local s = { a = a, b = b, bus = bus, len = math.max(math.abs(b.x - a.x), math.abs(b.z - a.z)), flowing = false, speed = 0, dir = 1, live = nil }
  cm.line(a, b, "cm_world:copper_live")
  for k = 0, 2 do
    local o = core.add_entity(a, "cm_world:chevron")
    if o then local e = o:get_luaentity(); e.seg = s; e.offset = k / 3 end
  end
  segs[name] = s
end
local function set_live(s, live)
  if s.live == live then return end
  cm.line(s.a, s.b, live and "cm_world:copper_live" or "cm_world:copper_dead")
  s.live = live
end

function P.build()
  cm.fill(at(-14, -22, 0), at(50, 20, 0), "default:silver_sandstone")
  -- Utility, T-1 and the main breaker (relay-main).
  cm.fill(at(-10, 16, 1), at(-10, 16, 9), "default:steelblock")
  P.util_lamp = at(-10, 16, 10)
  cm.fill(at(-11, 9, 1), at(-9, 11, 3), "default:steelblock")
  labels.utility = cm.label(at(-10, 16, 13), "Utility")
  labels.t1 = cm.label(at(-10, 10, 5), "T-1")
  P.breaker = at(-10, 5, 2)
  seg("utility", at(-10, 14), at(-10, 1), "msb")
  -- PV and its inverter.
  cm.fill(at(0, 12, 2), at(5, 17, 2), "cm_world:solid_info")
  cm.fill(at(2, 9, 1), at(2, 9, 2), "default:steelblock")
  labels.pv = cm.label(at(2, 14, 6), "PV")
  seg("pv", at(2, 8), at(2, 1), "msb")
  -- The BESS container and its state of charge.
  cm.fill(at(12, 11, 1), at(15, 14, 3), "default:obsidian_block")
  P.soc_col = at(17, 12, 1)
  labels.bess = cm.label(at(14, 12, 8), "BESS")
  seg("bess", at(13, 10), at(13, 1), "msb")
  -- The MSB.
  seg("msb", at(-12, 0), at(34, 0), "msb")
  labels.msb = cm.label(at(-14, 0, 4), "MSB")
  -- Branch buses with a feeder breaker lamp, and their loads.
  for bus, x in pairs(BRANCH_X) do
    if bus ~= "msb" then
      seg(bus, at(x, -1), at(x, -16), bus)
      labels[bus] = cm.label(at(x, -18, 3), string.upper(bus))
    end
  end
  -- Genset, ATS and EP-1.
  cm.fill(at(38, -20, 1), at(41, -17, 3), "default:obsidian_block")
  P.gen_lamp = at(39, -18, 4)
  P.fuel_col = at(43, -19, 1)
  labels.gen = cm.label(at(39, -19, 7), "Genset")
  P.ats = at(34, -8, 2)
  labels.ats = cm.label(at(34, -8, 5), "ATS")
  seg("ats_normal", at(34, -1), at(34, -7), "msb")
  seg("gen", at(38, -18), at(34, -9), "gen")
  seg("ep1", at(33, -10), at(20, -10), "ep1")
  labels.ep1 = cm.label(at(22, -12, 4), "EP-1")
end

-- A load is a column whose height follows log(kW) so a 120 kW boiler and a 0.3 kW cold room both show.
local function draw_load(bus, idx, name, kw, live)
  local key = bus .. "/" .. name
  local x, z
  if bus == "ep1" then x, z = 31 - idx * 3, -12 elseif bus == "msb" then x, z = 26 + idx * 3, -3 else x, z = BRANCH_X[bus] + 1, -3 - idx * 3 end
  local h = (kw and kw > 0) and math.min(14, 1 + math.floor(math.log(1 + kw) / math.log(2) * 1.6 + 0.5)) or 0
  local node = live and ((kw or 0) > 0 and "cm_world:solid_warn" or "cm_world:solid_off") or "cm_world:solid_off"
  local d = loads_drawn[key]
  if d and d.h == h and d.node == node then
    cm.relabel(d.label, name .. "\n" .. cm.fmt(kw, 1) .. " kW"); return
  end
  for y = 1, 15 do cm.setn(at(x, z, y), (y <= math.max(1, h)) and node or "air") end
  if not d then d = { label = cm.label(at(x, z, 2), name) } ; loads_drawn[key] = d end
  d.h, d.node = h, node
  d.label:set_pos(at(x, z, math.max(1, h) + 1.8))
  cm.relabel(d.label, name .. "\n" .. cm.fmt(kw, 1) .. " kW")
end

local function flow(name, kw, live)
  local s = segs[name]
  if not s then return end
  set_live(s, live)
  s.flowing = live and kw and math.abs(kw) > 0.05
  s.speed = s.flowing and (2 + math.sqrt(math.abs(kw))) or 0
  s.dir = (kw or 0) < 0 and -1 or 1
end

function P.apply(f)
  local p = f.power
  local buses = p.buses or {}
  local msb_live = buses.msb ~= false
  cm.setn(P.util_lamp, p.utility_ok and "cm_world:lamp_ok" or "cm_world:lamp_bad")
  cm.setn(P.breaker, p.utility_breaker_closed and "cm_world:lamp_ok" or "cm_world:lamp_bad")
  cm.relabel(labels.utility, "Utility " .. (p.utility_ok and "OK" or "LOST") .. "\nimport " .. cm.fmt(p.grid_kw, 1) .. " kW")
  cm.relabel(labels.pv, "PV " .. cm.fmt(p.pv_kw, 1) .. " kW\nlimit " .. cm.fmt(p.pv_limit_pct) .. "%  GHI " .. cm.fmt(p.ghi_wm2) .. " W/m2")
  cm.relabel(labels.bess, "BESS " .. cm.fmt(p.bess_kw, 1) .. " kW\nSOC " .. cm.fmt(p.bess_soc_pct) .. "%")
  local soc_h = math.floor((p.bess_soc_pct or 0) / 100 * 6 + 0.5)
  for y = 1, 6 do cm.setn({ x = P.soc_col.x, y = y, z = P.soc_col.z }, y <= soc_h and "cm_world:solid_ok" or "cm_world:solid_off") end
  cm.setn(P.gen_lamp, p.gen_running and "cm_world:lamp_ok" or "cm_world:lamp_off")
  local fuel_h = math.floor((p.gen_fuel_pct or 0) / 100 * 6 + 0.5)
  for y = 1, 6 do cm.setn({ x = P.fuel_col.x, y = y, z = P.fuel_col.z }, y <= fuel_h and "cm_world:solid_t1" or "cm_world:solid_off") end
  cm.relabel(labels.gen, "Genset " .. (p.gen_running and "running " .. cm.fmt(p.gen_kw, 1) .. " kW" or "off") .. "\nfuel " .. cm.fmt(p.gen_fuel_pct) .. "%")
  cm.setn(P.ats, p.ats_on_emergency and "cm_world:lamp_warn" or "cm_world:lamp_ok")
  cm.relabel(labels.ats, "ATS " .. (p.ats_on_emergency and "EMERGENCY" or "normal"))
  cm.relabel(labels.msb, "MSB " .. (msb_live and "live" or "DEAD") .. "\nload " .. cm.fmt(p.load_kw) .. " kW\n$" .. cm.fmt(p.rate_usd_kwh, 3) .. "/kWh" .. (p.onpeak and " on-peak" or ""))
  flow("utility", p.grid_kw, p.utility_ok and p.utility_breaker_closed)
  flow("pv", p.pv_kw, p.pv_connected ~= false)
  flow("bess", p.bess_kw, p.bess_connected ~= false)
  flow("gen", p.gen_kw, p.gen_running)
  local by_bus = {}
  for _, l in ipairs(p.loads or {}) do by_bus[l.bus] = by_bus[l.bus] or {}; table.insert(by_bus[l.bus], l) end
  local total_msb = 0
  for bus in pairs(BRANCH_X) do
    local kw = 0
    for _, l in ipairs(by_bus[bus] or {}) do kw = kw + (l.kw or 0) end
    total_msb = total_msb + kw
    if bus ~= "msb" then
      flow(bus, kw, buses[bus] ~= false)
      cm.relabel(labels[bus], string.upper(bus) .. (buses[bus] == false and " DEAD" or "") .. "\n" .. cm.fmt(kw, 1) .. " kW")
    end
  end
  local ep1_kw = p.ep1_kw or 0
  flow("msb", total_msb, msb_live)
  flow("ats_normal", (not p.ats_on_emergency) and ep1_kw or 0, msb_live and not p.ats_on_emergency)
  flow("ep1", ep1_kw, buses.ep1 ~= false)
  cm.relabel(labels.ep1, "EP-1" .. (buses.ep1 == false and " DEAD" or "") .. "\n" .. cm.fmt(ep1_kw, 1) .. " kW")
  for bus, list in pairs(by_bus) do
    table.sort(list, function(a, b) return a.name < b.name end)
    for i, l in ipairs(list) do draw_load(bus, i, l.name, l.kw, buses[bus] ~= false) end
  end
  return { grid_kw = p.grid_kw, msb_live = msb_live, ep1_live = buses.ep1 ~= false, ats_on_emergency = p.ats_on_emergency or false,
    gen_running = p.gen_running or false, dead_buses = (function() local n = 0; for _, v in pairs(buses) do if v == false then n = n + 1 end end; return n end)() }
end

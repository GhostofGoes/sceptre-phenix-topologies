-- The cellar (cm-viz's "cellar" and "fermenters" scenes share this one room).
-- Geometry from web/scenes/cellar.js: a 22 x 12 x 5.5 m room, four 30 bbl
-- unitanks, two bright tanks, four low CO2 sensors, two purge fans.
local C = {}
cm.cellar = C
local ROOM = { w = 22, d = 12, h = 5.5 }
local S = cm.S
local X0, X1 = math.floor(-ROOM.w / 2 * S), math.floor(ROOM.w / 2 * S)
local Z0, Z1 = math.floor(-ROOM.d / 2 * S), math.floor(ROOM.d / 2 * S)
local YH = math.floor(ROOM.h * S)
C.region = { { x = X0 - 12, y = -4, z = Z0 - 16 }, { x = X1 + 8, y = YH + 16, z = Z1 + 8 } }
C.bounds = { X0 = X0, X1 = X1, Z0 = Z0, Z1 = Z1, YH = YH }

local FV = { R = 1.0, H = 3.0, coneH = 0.9, legH = 0.6 }
local BBT = { R = 0.8, H = 2.4, coneH = 0.4, legH = 0.5 }
local VESSELS = {
  { id = "FV101", x = -8.0, z = -2.6, d = FV }, { id = "FV102", x = -4.2, z = -2.6, d = FV },
  { id = "FV103", x = -0.4, z = -2.6, d = FV }, { id = "FV104", x = 3.4, z = -2.6, d = FV },
  { id = "BBT201", x = 6.9, z = -3.6, d = BBT }, { id = "BBT202", x = 9.2, z = -3.6, d = BBT },
}
local SENSORS = { { -6, -5.85 }, { 1.5, -5.85 }, { -10.85, 1.0 }, { 5.5, -5.85 } }
local FANS = { -3.8, 2.6 }
local scene = { vessels = {}, sensors = {}, fans = {} }

-- A purge fan rotor that turns only while the fan really runs.
core.register_entity("cm_world:rotor", {
  initial_properties = { visual = "cube", visual_size = { x = 0.25, y = 1.8, z = 0.5 },
    textures = { "default_steel_block.png", "default_steel_block.png", "default_steel_block.png", "default_steel_block.png", "default_steel_block.png", "default_steel_block.png" },
    physical = false, pointable = false, static_save = false },
  on_step = function(self, dtime)
    self.omega = (self.omega or 0) + ((self.on and 8 or 0) - (self.omega or 0)) * math.min(1, dtime / 1.5)
    local r = self.object:get_rotation(); r.x = r.x + self.omega * dtime; self.object:set_rotation(r)
  end,
})

local function in_disc(dx, dz, r) return dx * dx + dz * dz <= r * r + r * 0.6 end
local function geom(v)
  local c = cm.B(v.x, 0, v.z)
  local r = math.max(1, math.floor(v.d.R * S + 0.5))
  local y_base = math.floor((v.d.legH + v.d.coneH) * S + 0.5)
  return c, r, y_base, y_base + math.floor(v.d.H * S + 0.5)
end

function C.build()
  cm.fill({ x = X0 - 1, y = -1, z = Z0 - 12 }, { x = X1 + 1, y = -1, z = Z1 + 1 }, "default:silver_sandstone")
  cm.fill({ x = X0 - 1, y = 0, z = Z1 + 1 }, { x = X1 + 1, y = YH, z = Z1 + 1 }, "default:stone_block")
  cm.fill({ x = X0 - 1, y = 0, z = Z0 - 1 }, { x = X0 - 1, y = YH, z = Z1 + 1 }, "default:stone_block")
  local door = cm.B(9.4, 0, -6)
  cm.fill({ x = door.x - 1, y = 0, z = Z1 + 1 }, { x = door.x + 1, y = 4, z = Z1 + 1 }, "default:obsidian_block")
  local g = cm.B(-0.5, 4.6, -5.6)
  cm.fill({ x = X0 + 2, y = g.y, z = g.z }, { x = X1 - 4, y = g.y, z = g.z }, "wool:cyan")
  scene.header = g
  for i, v in ipairs(VESSELS) do
    local c, r, y_base, y_top = geom(v)
    for dx = -r, r do for dz = -r, r do
      if in_disc(dx, dz, r) then
        local edge = not in_disc(math.abs(dx) + 1, dz, r) or not in_disc(dx, math.abs(dz) + 1, r)
        if edge then for y = y_base, y_top do cm.setn({ x = c.x + dx, y = y, z = c.z + dz }, "default:glass") end end
        cm.setn({ x = c.x + dx, y = y_top + 1, z = c.z + dz }, "default:steelblock")
      end
    end end
    local cone_r, leg_top = math.max(1, r - 1), math.floor(v.d.legH * S + 0.5) - 1
    for y = leg_top + 1, y_base - 1 do for dx = -cone_r, cone_r do for dz = -cone_r, cone_r do
      if in_disc(dx, dz, cone_r) then cm.setn({ x = c.x + dx, y = y, z = c.z + dz }, "default:steelblock") end
    end end end
    for _, o in ipairs({ { -r + 1, -r + 1 }, { r - 1, -r + 1 }, { 0, r - 1 } }) do
      cm.fill({ x = c.x + o[1], y = 0, z = c.z + o[2] }, { x = c.x + o[1], y = leg_top, z = c.z + o[2] }, "default:fence_wood")
    end
    -- PSV lamp on the top (left), spunding lamp (right), glycol valve lamps on the front.
    local sv = { c = c, r = r, y_base = y_base, y_top = y_top, kind = v.id:sub(1, 2),
      psv = { x = c.x - 1, y = y_top + 2, z = c.z }, spund = { x = c.x + 1, y = y_top + 2, z = c.z },
      jackets = { { x = c.x - 1, y = y_base + 2, z = c.z - r - 1 }, { x = c.x + 1, y = y_base + 2, z = c.z - r - 1 } } }
    -- Stagger the labels so neighbors do not overlap.
    sv.label = cm.label({ x = c.x, y = y_top + ((i % 2 == 0) and 6 or 4), z = c.z }, v.id)
    scene.vessels[v.id] = sv
  end
  for i, s in ipairs(SENSORS) do
    local p = cm.B(s[1], 0.45, s[2])
    if p.z > Z1 then p.z = Z1 end
    if p.x < X0 then p.x = X0 end
    cm.setn(p, "cm_world:lamp_ok")
    scene.sensors[i] = { p = p, label = cm.label({ x = p.x, y = p.y + 1.6, z = p.z - 0.6 }, "CO2-" .. i) }
  end
  for i, z in ipairs(FANS) do
    local p = cm.B(-ROOM.w / 2, 0.95, z); p.x = X0
    local rotor = core.add_entity({ x = p.x, y = p.y + 0.5, z = p.z }, "cm_world:rotor")
    local lamp = { x = p.x, y = p.y + 2, z = p.z }
    cm.setn(lamp, "cm_world:lamp_off")
    scene.fans[i] = { rotor = rotor, lamp = lamp, label = cm.label({ x = p.x + 2, y = p.y + 3.5, z = p.z }, "Purge fan " .. i) }
  end
  -- The HMI's layer, drawn as a ring of markers on the walls when it disagrees with the truth.
  scene.hmi_y = nil
end

local function layer_height(ppm) return 0.2 + 2.8 * math.max(0, math.min(1, (ppm - 1000) / 39000)) end
local function avg(t) local s = 0; for _, v in ipairs(t or {}) do s = s + v end; return #(t or {}) > 0 and s / #t or 0 end

local AIR = core.get_content_id("air")
local function apply_gas(ppm)
  local top = math.floor(layer_height(ppm) * S + 0.5)
  local gas = core.get_content_id("cm_world:gas_" .. cm.tier(ppm))
  local vm = core.get_voxel_manip()
  local e1, e2 = vm:read_from_map({ x = X0, y = 0, z = Z0 }, { x = X1, y = YH, z = Z1 })
  local area = VoxelArea:new({ MinEdge = e1, MaxEdge = e2 })
  local data = vm:get_data()
  local cells, changed = 0, 0
  for z = Z0, Z1 do for y = 0, YH do for x = X0, X1 do
    local i = area:index(x, y, z)
    local d = data[i]
    if d == AIR or cm.GAS[d] then
      local want = (y < top) and gas or AIR
      if d ~= want then data[i] = want; changed = changed + 1 end
      if y < top then cells = cells + 1 end
    end
  end end end
  if changed > 0 then vm:set_data(data); vm:write_to_map(false) end
  return top, cells, changed
end

local function mark_hmi(y)
  if scene.hmi_y == y then return end
  local function ring(yy, name)
    for x = X0, X1, 3 do cm.setn({ x = x, y = yy, z = Z1 }, name) end
    for z = Z0, Z1, 3 do cm.setn({ x = X0, y = yy, z = z }, name) end
  end
  if scene.hmi_y then ring(scene.hmi_y, "default:stone_block") end
  if y then ring(y, "cm_world:lamp_info") end
  scene.hmi_y = y
end

function C.apply(f)
  local air = f.cellar.air
  for i, ppm in ipairs(air.sensors_ppm or {}) do
    local s = scene.sensors[i]
    if s then
      cm.setn(s.p, "cm_world:lamp_" .. cm.tier(ppm))
      cm.relabel(s.label, "CO2-" .. i .. "  " .. cm.fmt(ppm) .. " ppm")
    end
  end
  local truth = avg(air.sensors_ppm)
  local top, cells, changed = apply_gas(truth)
  -- The HMI's layer, when the operator view disagrees by more than a block.
  local op = f.operator
  local hmi_top = op and op.co2_sensors_ppm and math.floor(layer_height(avg(op.co2_sensors_ppm)) * S + 0.5) or nil
  mark_hmi((hmi_top and math.abs(hmi_top - top) >= 1) and hmi_top or nil)
  for i, fan in ipairs(air.fans or {}) do
    local sf = scene.fans[i]
    if sf then
      local e = sf.rotor and sf.rotor:get_luaentity()
      if e then e.on = fan.running end
      cm.setn(sf.lamp, fan.running and "cm_world:lamp_ok" or "cm_world:lamp_bad")
      cm.relabel(sf.label, "Purge fan " .. i .. "\n" .. (fan.running and "running" or fan.commanded and "commanded, stopped" or "off"))
    end
  end
  local glycol = f.cellar.glycol_supply_f
  local hdr = "cm_world:fill_" .. cm.temp_idx(glycol)
  if scene.header_node ~= hdr then
    cm.fill({ x = X0 + 2, y = scene.header.y, z = scene.header.z }, { x = X1 - 4, y = scene.header.y, z = scene.header.z }, hdr)
    scene.header_node = hdr
  end
  local psv_lifted = 0
  for _, v in ipairs(f.cellar.vessels or {}) do
    local sv = scene.vessels[v.id]
    if sv then
      local fill_top = sv.y_base + math.floor((sv.y_top - sv.y_base) * (v.level_pct or 0) / 100 + 0.5)
      local node = "cm_world:fill_" .. cm.temp_idx(v.temp_f)
      local r = sv.r - 1
      for dx = -r, r do for dz = -r, r do
        if in_disc(dx, dz, r) then
          for y = sv.y_base, sv.y_top do cm.setn({ x = sv.c.x + dx, y = y, z = sv.c.z + dz }, y <= fill_top and node or "air") end
        end
      end end
      cm.setn(sv.psv, v.psv_lifted and "cm_world:lamp_bad" or "cm_world:lamp_off")
      cm.setn(sv.spund, v.spund_open and "cm_world:lamp_warn" or "cm_world:lamp_off")
      for j, jp in ipairs(sv.jackets) do
        local open = (v.glycol_valves or {})[j]
        cm.setn(jp, open == nil and "air" or open and "cm_world:lamp_info" or "cm_world:lamp_off")
      end
      if v.psv_lifted then psv_lifted = psv_lifted + 1 end
      local p = v.pressure_psig
      local mark = (v.psv_lifted and "\nPSV LIFTED" or "") .. ((v.leak_gps or 0) > 0 and ("\nLEAK " .. cm.fmt(v.leak_gps) .. " g/s") or "")
        .. ((v.spund_sp_psig or 0) > 15 and ("\nspund SP " .. cm.fmt(v.spund_sp_psig, 1) .. " > MAWP") or "")
      cm.relabel(sv.label, v.label .. "  " .. cm.fmt(v.temp_f, 1) .. " F\n" .. cm.fmt(p, 1) .. " psig  " .. cm.fmt(v.level_pct) .. "%" .. mark)
      local vent = (v.leak_gps or 0) + (v.psv_gps or 0) + (v.spund_gps or 0)
      if vent > 0 then
        core.add_particlespawner({ amount = math.min(120, 20 + math.floor(vent)), time = cm.settings.poll_s + 0.1,
          minpos = { x = sv.c.x - 0.6, y = sv.y_top + 2, z = sv.c.z - 0.6 }, maxpos = { x = sv.c.x + 0.6, y = sv.y_top + 3, z = sv.c.z + 0.6 },
          minvel = { x = -0.4, y = 1.0, z = -0.4 }, maxvel = { x = 0.4, y = 2.2, z = 0.4 }, minexptime = 1.5, maxexptime = 3,
          minsize = 4, maxsize = 8, texture = "default_cloud.png^[opacity:200" })
      end
    end
  end
  return { avg_ppm = truth, tier = cm.tier(truth), layer_blocks = top, gas_cells = cells, gas_changed = changed,
    hmi_layer_blocks = hmi_top, fans_running = (air.fans[1] or {}).running and (air.fans[2] or {}).running or false,
    psv_lifted = psv_lifted }
end

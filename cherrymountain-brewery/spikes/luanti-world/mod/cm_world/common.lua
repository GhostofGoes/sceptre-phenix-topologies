-- Shared nodes, colors and helpers. One meter of a cm-viz scene is cm.S blocks.
cm.S = 2
-- cm-viz meters -> blocks. cm-viz's -z (the back wall) becomes +Z, so the view
-- from the front matches the browser's default camera.
function cm.B(x, y, z, o)
  o = o or { x = 0, y = 0, z = 0 }
  return { x = o.x + math.floor(x * cm.S + 0.5), y = o.y + math.floor(y * cm.S + 0.5), z = o.z + math.floor(-z * cm.S + 0.5) }
end
function cm.fmt(v, d) return v and string.format("%." .. (d or 0) .. "f", v) or "-" end

-- Colors from cm-viz's web/scenes/common.js, so both views mean the same thing.
cm.TEMP_STOPS = { { 28, "2f6fe0" }, { 40, "37a6d9" }, { 55, "d9b54a" }, { 66, "e39a2d" }, { 75, "e0602a" }, { 85, "d22f2f" } }
cm.COLOR = { ok = "4cc38a", t1 = "ffe04a", t2 = "ff8c1a", t3 = "ff2b2b", off = "555b62", bad = "ff3b30", warn = "ffb020", info = "6aa9ff" }
function cm.tier(ppm) return ppm >= 30000 and "t3" or ppm >= 15000 and "t2" or ppm >= 5000 and "t1" or "ok" end
function cm.temp_idx(f)
  local best = 1
  if f then for i, s in ipairs(cm.TEMP_STOPS) do if f >= s[1] then best = i end end end
  return best
end
function cm.pressure_key(psig)
  if not psig or psig < 0.5 then return "off" end
  return psig < 13.5 and "ok" or psig < 14.9 and "warn" or "bad"
end

local function tex(hex, base, ratio) return (base or "default_clay.png") .. "^[colorize:#" .. hex .. ":" .. (ratio or 230) end
local hidden = { not_in_creative_inventory = 1, oddly_breakable_by_hand = 3 }
for i, s in ipairs(cm.TEMP_STOPS) do
  core.register_node("cm_world:fill_" .. i, { description = "Beer at " .. s[1] .. " F", tiles = { tex(s[2]) }, light_source = 3, groups = hidden })
end
for k, hex in pairs(cm.COLOR) do
  core.register_node("cm_world:lamp_" .. k, { description = "Lamp " .. k, tiles = { tex(hex, "default_meselamp.png") }, light_source = 12, groups = hidden })
  core.register_node("cm_world:solid_" .. k, { description = "Block " .. k, tiles = { tex(hex) }, groups = hidden })
  core.register_node("cm_world:gas_" .. k, {
    description = "CO2 layer " .. k, drawtype = "glasslike", tiles = { tex(hex, "default_clay.png", 255) .. "^[opacity:110" },
    use_texture_alpha = "blend", paramtype = "light", sunlight_propagates = true, walkable = false, pointable = false,
    buildable_to = true, groups = hidden,
  })
end
core.register_node("cm_world:copper_live", { description = "Live conductor", tiles = { "default_copper_block.png" }, light_source = 4, groups = hidden })
core.register_node("cm_world:copper_dead", { description = "Dead conductor", tiles = { "default_copper_block.png^[colorize:#555b62:200" }, groups = hidden })
cm.GAS = {}
for k in pairs(cm.COLOR) do cm.GAS[core.get_content_id("cm_world:gas_" .. k)] = true end

-- Floating labels: an invisible entity with a nametag.
core.register_entity("cm_world:label", {
  initial_properties = { visual = "sprite", textures = { "blank.png" }, physical = false, pointable = false, static_save = false,
    nametag_color = "#e6e9ed", nametag_bgcolor = "#1b2026c8" },
})
function cm.label(p, text)
  local o = core.add_entity(p, "cm_world:label")
  if o then o:set_properties({ nametag = text }) end
  return o
end
function cm.relabel(o, text) if o and o:get_pos() then o:set_properties({ nametag = text }) end end

function cm.setn(p, name) core.set_node(p, { name = name }) end
function cm.fill(a, b, name)
  for x = math.min(a.x, b.x), math.max(a.x, b.x) do for y = math.min(a.y, b.y), math.max(a.y, b.y) do
    for z = math.min(a.z, b.z), math.max(a.z, b.z) do core.set_node({ x = x, y = y, z = z }, { name = name }) end
  end end
end
-- A straight run of blocks between two points (3D DDA), for cables and conductors.
function cm.line(a, b, name)
  local d = math.max(math.abs(b.x - a.x), math.abs(b.y - a.y), math.abs(b.z - a.z), 1)
  for i = 0, d do
    cm.setn({ x = math.floor(a.x + (b.x - a.x) * i / d + 0.5), y = math.floor(a.y + (b.y - a.y) * i / d + 0.5), z = math.floor(a.z + (b.z - a.z) * i / d + 0.5) }, name)
  end
end

local hud = {}
function cm.hud_set(lines, stale)
  if lines then cm.hud_lines, cm.hud_stale = lines, stale end
  lines, stale = cm.hud_lines or {}, cm.hud_stale
  local text = table.concat(lines, "\n")
  for _, p in ipairs(core.get_connected_players()) do
    local n = p:get_player_name()
    local color = stale and 0x9AA4AE or 0xE6E9ED
    if hud[n] then p:hud_change(hud[n], "text", text); p:hud_change(hud[n], "number", color)
    else hud[n] = p:hud_add({ hud_elem_type = "text", position = { x = 0.01, y = 0.06 }, alignment = { x = 1, y = 1 }, number = color, text = text }) end
  end
end
function cm.write_world_file(name, data)
  local fh = io.open(core.get_worldpath() .. "/" .. name, "w")
  if fh then fh:write(data); fh:close() end
end

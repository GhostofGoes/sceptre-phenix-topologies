-- The white cell's console: one button per allowlisted action. A press names
-- the action to the relay (harness.py in the spike, a relay to the sim-ctl
-- event API in the range); the mod never writes to the plant itself. The relay
-- refuses anything off its list, and logs who pressed what.
local K = {}
cm.controls = K
K.log = {}
local ACTIONS = {
  { id = "fans-on", text = "Purge fans ON", color = "ok" },
  { id = "fans-off", text = "Purge fans OFF", color = "bad" },
  { id = "co2-release", text = "CO2 release FV-102", color = "t2" },
  { id = "spunding-attack", text = "Spunding SP 30 psig", color = "warn" },
  { id = "utility-open", text = "Utility breaker OPEN", color = "t3" },
  { id = "utility-close", text = "Utility breaker CLOSE", color = "info" },
}

function K.press(action, who)
  local body = core.write_json({ action = action, by = who })
  cm.http.fetch({ url = cm.settings.control_url, method = "POST", data = body, timeout = 3,
    extra_headers = { "Content-Type: application/json" } }, function(res)
    local ok = res.succeeded and res.code == 202
    K.last_text = (ok and "CONTROL: " or "CONTROL REFUSED: ") .. action .. " by " .. who .. (ok and "" or (" (" .. tostring(res.code) .. ")"))
    table.insert(K.log, { action = action, by = who, ok = ok, code = res.code })
    core.log("action", "cm_world: " .. K.last_text)
    core.chat_send_all(K.last_text)
  end)
end

for _, a in ipairs(ACTIONS) do
  core.register_node("cm_world:btn_" .. a.id:gsub("-", "_"), {
    description = a.text, tiles = { "default_steel_block.png", "default_steel_block.png", "default_meselamp.png^[colorize:#" .. cm.COLOR[a.color] .. ":220" },
    paramtype2 = "facedir", light_source = 8, groups = { not_in_creative_inventory = 1, oddly_breakable_by_hand = 3 },
    on_rightclick = function(_, _, clicker) if clicker and clicker:is_player() then K.press(a.id, clicker:get_player_name()) end end,
    on_punch = function(_, _, puncher) if puncher and puncher:is_player() then K.press(a.id, puncher:get_player_name()) end end,
  })
end

function K.build()
  local b = cm.cellar.bounds
  local z = b.Z0 - 8
  local x0 = -math.floor((#ACTIONS - 1) * 4 / 2)
  cm.fill({ x = x0 - 3, y = 0, z = z }, { x = x0 + (#ACTIONS - 1) * 4 + 3, y = 0, z = z + 1 }, "default:obsidian_block")
  for i, a in ipairs(ACTIONS) do
    local p = { x = x0 + (i - 1) * 4, y = 1, z = z }
    core.set_node(p, { name = "cm_world:btn_" .. a.id:gsub("-", "_"), param2 = 0 })
    cm.label({ x = p.x, y = p.y + 1.6, z = p.z }, a.text)
  end
  cm.label({ x = 0, y = 4, z = z }, "White cell console (actions go to the relay, not the plant)")
end

core.register_chatcommand("cm", {
  params = "<action>", description = "Send an allowlisted action to the relay (fans-on, utility-open, ...)",
  func = function(name, param) K.press(param, name); return true, "sent " .. param end,
})

-- Test hook: press a button by its own handler after N seconds ("fans-on@40,utility-open@60").
local auto = core.settings:get("cm_world.autopress")
if auto then
  for item in auto:gmatch("[^,]+") do
    local action, t = item:match("([%w%-]+)@(%d+)")
    if action then core.after(tonumber(t), function() K.press(action, "autopress") end) end
  end
end

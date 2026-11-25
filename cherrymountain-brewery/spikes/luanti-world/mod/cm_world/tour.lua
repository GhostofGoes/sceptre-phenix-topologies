-- Camera presets, a /cmcam command, and a timed tour for screenshots: the tour
-- writes the preset it is on to cm_world_tour.txt in the world directory, so a
-- script outside the game knows when to take each picture.
local PRESETS = {
  cellar = { 13, 13, -17, 36, 22 },
  fermenters = { 0, 9, -10, 20, 12 },
  controls = { 2, 6, -30, 0, 14 },
  power = { 84, 20, -30, 0, 34 },
  topology = { 0, 40, -190, 0, 20 },
}
cm.presets = PRESETS
local function place(p, name)
  local c = PRESETS[name]
  if not c then return false end
  if name == "topology" then
    local ctr = cm.topology.center
    c = { ctr.x + 62, ctr.y + 40, ctr.z + 58, 131, 25 }
  end
  p:set_pos({ x = c[1], y = c[2], z = c[3] })
  p:set_look_horizontal(math.rad(c[4])); p:set_look_vertical(math.rad(c[5]))
  return true
end
core.register_chatcommand("cmcam", { params = "<" .. table.concat((function() local t = {}; for k in pairs(PRESETS) do t[#t + 1] = k end; return t end)(), "|") .. ">",
  func = function(name, param) local p = core.get_player_by_name(name); return p and place(p, param), param end })

core.register_on_joinplayer(function(p)
  core.set_timeofday(0.5)
  local privs = core.get_player_privs(p:get_player_name()); privs.fly = true; privs.noclip = true; privs.fast = true
  core.set_player_privs(p:get_player_name(), privs)
  p:set_physics_override({ gravity = 0 })
  p:hud_set_flags({ hotbar = false, healthbar = false, breathbar = false, wielditem = false, crosshair = false, minimap = false })
  p:set_sky({ type = "plain", base_color = "#1b2026", clouds = false })
  p:set_sun({ visible = false, sunrise_visible = false }); p:set_moon({ visible = false }); p:set_stars({ visible = false })
  local tour = core.settings:get("cm_world.tour")
  local start = core.settings:get("cm_world.camera_preset") or "cellar"
  core.after(1, function() if p:is_player() then place(p, start) end end)
  if tour then
    -- "cellar@25,power@60": go to a preset at a time (s after joining).
    local last_t = 0
    for name, t in tour:gmatch("([%w]+)@(%d+)") do
      t = tonumber(t); last_t = math.max(last_t, t)
      core.after(t, function()
        if not p:is_player() then return end
        place(p, name)
        core.after(4, function() cm.write_world_file("cm_world_tour.txt", name .. "@" .. t .. " seq " .. tostring(cm.last.seq)) end)
      end)
    end
    core.after(last_t + 8, function() cm.write_world_file("cm_world_tour.txt", "done") end)
  end
end)

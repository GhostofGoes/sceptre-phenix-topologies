-- cm_world: the CherryMountain plant in Luanti, live from cm-viz's /frame.json.
-- Spike code (DESIGN.md §15.10 follow-up). Modules:
--   common.lua    nodes, colors, helpers, the scale
--   cellar.lua    the cellar and fermenters scenes
--   power.lua     the plant power scene
--   controls.lua  the white cell's console (allowlisted actions through a relay)
--   topology.lua  the phenix topology as a city (topology.json from tools/topo_gen.py)
--   tour.lua      camera presets and the screenshot tour
cm = {}
local MP = core.get_modpath("cm_world")
cm.http = core.request_http_api()
assert(cm.http, "cm_world: add cm_world to secure.http_mods")
cm.settings = {
  frame_url = core.settings:get("cm_world.frame_url") or "http://127.0.0.1:8090/frame.json",
  control_url = core.settings:get("cm_world.control_url") or "http://127.0.0.1:8092/control",
  poll_s = tonumber(core.settings:get("cm_world.poll_s") or "0.5"),
  write_status = core.settings:get_bool("cm_world.write_status", false),
}
dofile(MP .. "/common.lua")
dofile(MP .. "/cellar.lua")
dofile(MP .. "/power.lua")
dofile(MP .. "/controls.lua")
dofile(MP .. "/topology.lua")
dofile(MP .. "/tour.lua")

-- Generate the map around every scene once, then build the static parts.
local REGIONS = { cm.cellar.region, cm.power.region, cm.topology.region }
local pending = #REGIONS
cm.ready = false
core.after(0, function()
  for _, r in ipairs(REGIONS) do
    core.emerge_area(r[1], r[2], function(_, _, remaining)
      if remaining == 0 then
        pending = pending - 1
        if pending == 0 then
          local t0 = core.get_us_time()
          cm.cellar.build(); cm.power.build(); cm.controls.build(); cm.topology.build()
          cm.ready = true
          core.log("action", string.format("cm_world: static scenes built in %.0f ms", (core.get_us_time() - t0) / 1000))
        end
      end
    end)
  end
end)

-- Poll the frame; apply only a new one. A failed fetch or a stale frame greys the HUD.
local busy, acc, last_ok = false, 0, 0
cm.last = { seq = -1 }
core.register_globalstep(function(dtime)
  acc = acc + dtime
  if not cm.ready or busy or acc < cm.settings.poll_s then return end
  acc, busy = 0, true
  cm.hud_set()
  cm.http.fetch({ url = cm.settings.frame_url, timeout = 3 }, function(res)
    busy = false
    local now = core.get_us_time() / 1e6
    if not res.succeeded or res.code ~= 200 then
      if now - last_ok > 5 then cm.hud_set({ "cm-viz feed lost: " .. tostring(res.code) }, true) end
      return
    end
    last_ok = now
    local f = core.parse_json(res.data)
    if not (f and f.cellar and f.power) or f.seq == cm.last.seq then return end
    local t0 = core.get_us_time()
    local ok, err = pcall(function()
      local c = cm.cellar.apply(f)
      local p = cm.power.apply(f)
      cm.last = { seq = f.seq, sim_t_s = f.sim_t_s, cellar = c, power = p, findings = #(f.findings or {}),
        controls = cm.controls.log, apply_ms = (core.get_us_time() - t0) / 1000 }
    end)
    if not ok then core.log("error", "cm_world: " .. tostring(err)); return end
    local lines = { "CherryMountain (cm-viz frame " .. f.seq .. ", " .. f.source .. ")",
      "Sim time " .. tostring(f.sim_time) .. "    CO2 " .. cm.fmt(cm.last.cellar.avg_ppm) .. " ppm    grid " .. cm.fmt(f.power.grid_kw) .. " kW" }
    for _, fd in ipairs(f.findings or {}) do lines[#lines + 1] = string.upper(fd.level) .. ": " .. fd.text end
    if cm.controls.last_text then lines[#lines + 1] = cm.controls.last_text end
    cm.hud_set(lines, f.stale)
    if cm.settings.write_status then cm.write_world_file("cm_world_status.json", core.write_json(cm.last)) end
  end)
end)

-- The phenix topology as a city (tools/topo_gen.py writes topology.json):
-- a terrace per Purdue level, the internet highest and the field networks on
-- the ground; a platform per VLAN; a tower per host; a cable from each router,
-- firewall or dual-homed host to every other VLAN it touches.
local T = {}
cm.topology = T
local fh = io.open(core.get_modpath("cm_world") .. "/topology.json", "r")
local data = fh and core.parse_json(fh:read("*a")) or { platforms = {}, towers = {}, cables = {}, levels = {} }
if fh then fh:close() end
T.data = data

local x0, y0, z0, x1, y1, z1 = 1e9, 1e9, 1e9, -1e9, -1e9, -1e9
for _, p in ipairs(data.platforms) do
  x0, x1 = math.min(x0, p.x0), math.max(x1, p.x1); z0, z1 = math.min(z0, p.z0), math.max(z1, p.z1)
  y0, y1 = math.min(y0, p.y), math.max(y1, p.y + 12)
end
T.region = { { x = x0 - 12, y = y0 - 4, z = z0 - 8 }, { x = x1 + 12, y = y1 + 8, z = z1 + 8 } }
T.center = { x = (x0 + x1) / 2, y = (y0 + y1) / 2, z = (z0 + z1) / 2 }

local LEVEL_NODE = { "wool:red", "wool:orange", "wool:yellow", "wool:green", "wool:cyan", "wool:blue" }
local ROLE_CAP = { router = "cm_world:lamp_info", firewall = "cm_world:lamp_t3", plc = "cm_world:lamp_t1", rio = "cm_world:lamp_ok",
  hmi = "cm_world:lamp_warn", sim = "wool:violet", soc = "wool:magenta", server = "default:meselamp" }
local CABLE = { router = "wool:white", firewall = "wool:pink", plc = "wool:yellow", rio = "wool:green" }

function T.build()
  for _, p in ipairs(data.platforms) do
    local node = LEVEL_NODE[p.level + 1] or "wool:grey"
    cm.fill({ x = p.x0, y = p.y, z = p.z0 }, { x = p.x1, y = p.y, z = p.z1 }, node)
    -- Pillars down to the ground so each terrace stands on something.
    for _, c in ipairs({ { p.x0, p.z0 }, { p.x1, p.z0 }, { p.x0, p.z1 }, { p.x1, p.z1 } }) do
      if p.y > 0 then cm.fill({ x = c[1], y = 0, z = c[2] }, { x = c[1], y = p.y - 1, z = c[2] }, "default:stone_block") end
    end
    cm.label({ x = p.x1 + 0.5, y = p.y + 1.2, z = p.z1 + 0.5 }, p.vlan)
  end
  for _, t in ipairs(data.towers) do
    cm.fill({ x = t.x, y = t.y, z = t.z }, { x = t.x, y = t.y + t.h - 2, z = t.z }, t.role == "firewall" and "default:obsidian_block" or "default:steelblock")
    cm.setn({ x = t.x, y = t.y + t.h - 1, z = t.z }, ROLE_CAP[t.role] or "default:meselamp")
    cm.label({ x = t.x, y = t.y + t.h + 0.8, z = t.z }, t.host)
  end
  for _, c in ipairs(data.cables) do
    cm.line({ x = c.from[1], y = c.from[2], z = c.from[3] }, { x = c.to[1], y = c.to[2], z = c.to[3] }, CABLE[c.role] or "wool:white")
  end
  for _, l in ipairs(data.levels) do cm.label({ x = x1 + 6, y = l.y + 2, z = l.z }, l.title) end
  cm.fill({ x = x0 - 10, y = -1, z = z0 - 6 }, { x = x1 + 10, y = -1, z = z1 + 6 }, "default:silver_sandstone")
end

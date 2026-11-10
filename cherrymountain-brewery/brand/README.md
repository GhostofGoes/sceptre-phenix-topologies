# CherryMountain Brewery brand

The brewery's mark and colors, for every screen of the range that shows an icon or
has a frame to color. One source, here; the screens take copies of these files.

| File | Use |
|---|---|
| `icon.svg` | The mark: a phoenix rising behind the summit. Use at 48 px and above |
| `icon-small.svg` | The mark for 16 to 32 px (browser tabs, taskbars): the same bird and summit without the fine parts |
| `icon-<n>.png`, `favicon.ico` | Rendered from the two SVGs (16 and 32 from the small one). The ICO holds 16, 32, and 48 |
| `wallpaper.png`, `wallpaper-1080p.png` | The desktop wallpaper at 3840x2160 and 1920x1080: the Front Range at dusk, the phoenix of the mark in front of the sun, and the brewery in the foothills. The upper left is dark sky, so desktop icons stay readable; the name is at the lower left |
| `wallpaper.cjs` | Draws the wallpaper as an SVG from a fixed seed, so it comes out the same every time |
| `tokens.json` | The colors by name, what each is for, and the colors reserved for alarms |
| `render_assets.cjs` | Renders the PNGs, the ICO, and both wallpapers again: `node brand/render_assets.cjs` (Playwright with Chromium). Run it by hand after a change to an SVG or the tokens, and commit the results |

## Colors (the "Phenix Fire" scheme)

| Name | Value | For |
|---|---|---|
| ink | `#1B1B1F` | Headers, dark page backgrounds, text on light |
| coal | `#2A2A30` | Menus and panels on a dark page |
| ash | `#55555D` | Secondary text and marks |
| ember | `#E2552B` | The brand color: a 3 px stripe under a header, the primary button, the first chart series |
| flame | `#F4A93B` | Links and highlights on dark, the second chart series |
| paper | `#EFEFEF` | Text on dark, light surfaces |
| steel, moss | `#5B8DB8`, `#7A9A5B` | Further chart series only |

## Rules

1. **Brand color goes on the frame of a screen, never in the process area.** Header, menu,
   sign-in page, wallpaper, chart lines. Process graphics stay gray (ISA-101), so color in
   the process area always means something.
2. **Alarm colors are reserved** (`tokens.json`, `reserved_for_alarms`): the reds, the yellow,
   and the light blue of the alarm levels are never used for decoration. Ember is close to
   alarm red, so on an HMI it is only the stripe, the icon, and a primary button.
3. **The mark is not redrawn or recolored.** On a dark frame it needs no backing: its outer
   ring is ink and its gold ring gives it an edge.
4. **The name** is written "CherryMountain Brewery", one word then one word.

The Malcolm and Wazuh consoles keep their own look, as a real SOC's tools would.

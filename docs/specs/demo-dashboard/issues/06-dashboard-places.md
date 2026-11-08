# 06 Dashboard: label Boulder Springs Ranch, clean up places

Status: resolved
Type: task
Blocked by: none

Spec: `../spec.md` (Dashboard → Places; story 11).

## What
- Remove the `sites` layer (`sites.geojson`: observation deck next to the crew, Hotshot Journey Trailhead (2016), town centre). The lunch spot is not drawn (it isn't today either).
- HTML label markers (no MapLibre text: no glyphs offline) from `scenario.geojson`: "Boulder Springs Ranch" + a 100 m safety zone circle (fill + outline; radius from the feature's `radius_m`), "2013 deployment site".
- Keep the 2013 descent line (dotted white); label it "2013 crew route" near its start.
- Update the map legend/footer to match.

## Files
`dashboard/src/App.tsx`, `dashboard/src/styles.css`.

## Acceptance (manual)
- The ranch label and circle sit on the ranch end of the ranch route; no yellow dot next to the crew.

## Comments

- Done (Nov 8 ~06:35, Vivek's round-2 list): HTML labels "Boulder Springs Ranch" + 100 m safety zone circle (fill + dashed line), "2013 deployment site" (red dot + label); `sites.geojson` layer removed. The descent line is labelled in the map legend ("2013 crew route") instead of on the map.

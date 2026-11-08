# LCES Live

Live wildfire escape-route safety: how much time a crew has between reaching safety and the fire reaching their path.

## Sensing

**Sensor node**:
A physical weather sensor (N1–N3) given a fake GPS position at a real Yarnell spot.
_Avoid_: node (when meaning a grid point), station

**Fire point**:
A location where fire is known to be burning, created by a hot sensor node reading or by a manual ignition.
_Avoid_: hot spot, detection

**Fire front**:
The edge between cells the fire has reached and cells it has not.
_Avoid_: perimeter (that is the final 2013 outline)

**Burning**:
Ground the fire reached less than 5 minutes ago; impassable to the crew.

**Ignition time**:
When a fire point started burning; fire spread is measured from it.

**Stale**:
A sensor node that has missed 3 heartbeats at its own interval; the engine assumes worst-case weather for it.
_Avoid_: dead, offline

**Aging**:
A sensor node that has missed 1 heartbeat but is not yet stale.

## Terrain

**Cell**:
One 10 m × 10 m square of the routing area; the unit at which fire and crew arrival times are known.
_Avoid_: node, pixel

**Graph vertex**:
A point the crew can route through: either a cell centre or a point on a trail.
_Avoid_: node

**Trail**:
An existing road, track or path the crew can walk at full speed.

## Escape

**Crew**:
The single firefighting crew being protected; its position comes from the scenario.

**Safety zone**:
A place the crew can survive the fire without shelter: Boulder Springs Ranch or the black.

**The black**:
Ground whose flames went out after it burned (5 minutes after fire arrival); a safety zone.
_Avoid_: burn scar, burned area

**Escape route**:
The fastest current path from the crew to one specific safety zone; there is one per safety zone.
_Avoid_: route (alone), path

**Active route**:
The escape route the crew is following: the fastest route that isn't red at Start, then it changes only by a reroute.

**Reroute**:
A change of the active route, either a new path to the same safety zone or a switch to another safety zone.

**Margin**:
Fire arrival time minus crew arrival time, taken at the worst cell along an escape route.

## Scenarios

**2013 replay**:
The demo scenario using the pre-fire chaparral of the 2013 Yarnell Hill fire.
_Avoid_: historical mode

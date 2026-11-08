# Route in memory with scipy, not pgRouting

Escape routes are computed with `scipy.sparse.csgraph.dijkstra` on an in-memory graph built once from the rasters, not with pgRouting in PostGIS. Each recompute needs 3–4 shortest-path runs over ~40k cells and ~160k edges within a 2 s budget; pgRouting reloads every edge per query and was never timed on the Pi, while the fire arrival times are already numpy arrays. Fire closures are applied by dropping edges from the in-memory edge list, so there is no `fire_blocked` table. PostGIS still holds the rasters and map layers.

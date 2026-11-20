# Changelog

All notable changes to this project are documented here. Versions follow semantic versioning; a release is cut on request.

## Unreleased

## 0.2.4 - 2026-11-20

### Fixed
- A GEOS error while rounding or merging the geometries of a tile no longer stops a build: sea pieces that are not valid are made valid before they are merged; a failed merge falls back to a merge without the grid, then to the separate pieces; a geometry that cannot be rounded is made valid and rounded again, then left out. Without any such error, the tiles are unchanged, byte for byte.

### Changed
- The build log counts these fallbacks in one line and names the first tiles; there is no line when there are none.
- An error while encoding a tile now names the tile (z/x/y) and the layer.

## 0.2.3 - 2026-11-19

### Fixed
- A border added by a theatre rule (`frontieres_ajoutees`) is now recognised by the relations it belongs to, even when the way itself carries no `boundary` tag, as is common in OpenStreetMap; a way tagged `maritime=yes` is still left out.

### Changed
- A `frontieres_ajoutees` rule whose relations share no way between `a` and `b` is refused as soon as the relations are read, before the long first pass.
- The log gives one line per OpenStreetMap extract read, with its rank and duration.
- A Copernicus DEM tile is tried five times, 35 s apart, on a network error; a 404 still means no tile, and any other HTTP status still stops the build.

## 0.2.2 - 2026-11-18

### Changed
- An OpenStreetMap extract served by a mirror on another host is no longer checked against an MD5 checksum, since the original host publishes none for redirected extracts; the checks on status, size and header remain, and the redirection must still stay on HTTPS, port 443, without credentials.

## 0.2.1 - 2026-11-18

### Fixed
- A theatre build now follows the redirection of an OpenStreetMap extract to a mirror on another host (HTTPS only), and keeps the file only if it matches the MD5 checksum that the original host publishes next to the extract (`<address>.md5`); the build stops otherwise. Elevation tiles still follow no redirection to another host.

## 0.2.0 - 2026-11-18

### Added
- Releases are published by a workflow: pushing a branch `release/v<x.y.z>` with the release text and a manifest (and any files to attach) creates the release and its tag. The basemap factory does not change.
- Finished branches are deleted by a workflow: once a day, `.github/workflows/menage-branches.yml` with `outils/menage_branches.py` deletes a branch whose last commit is at least 7 days old if it is finished — its whole content is already in `main`, its release tag exists, or it is a release trial. Every other branch is kept, and every decision is listed on the run page. Run by hand, the workflow only lists its decisions. The basemap factory does not change.
- Theatre descriptors may carry an `exclusions` key to correct the present-day map for an earlier period: motorways removed by their `ref` (everywhere, or only within a box), national borders no longer drawn between countries of a given group, and borders drawn between two groups of administrative areas. A rule that matches nothing stops the build, and the build log reports, rule by rule, what was removed or added. `germanycw` and `caucasus` use it; a descriptor without it builds byte for byte as before.

## 0.1.0 - 2026-11-14

### Added
- `fabrique`: builds a paper-style basemap for any area from OpenStreetMap extracts and the Copernicus DEM (GLO-30, with GLO-90 where GLO-30 is unavailable): vector tiles (`fond.pmtiles`, zooms 5–14), shaded relief (`relief.pmtiles`, zooms 5–12), light and dark styles, sprite, glyphs, licence notices and a build manifest.
- Hand-drawn look baked into the geometry, per zoom band; true UTM/MGRS grid across zone boundaries.
- No village names or village markers at any zoom; towns and cities only.
- Reproducible builds: the same inputs and tools give byte-identical outputs.
- Bounded memory: the build works by blocks (`--bloc-zoom`, 7 to 10) with an on-disk spatial store; the output is byte-identical whatever the block size.
- Names: first of `name:en`, `int_name`, `name`; names that a font cannot draw are left silent; composed glyph stack `Special Elite,JetBrains Mono`.
- `extraire-overpass` to fetch a small test area; `verifier` to check a build; a local test page and renderer.
- `theatre` command: builds a whole theatre from its descriptor (`theatres/<name>.json`) with given output, working and cache folders, for unattended runs in a locked-down container; progress lines on standard output; the build verifies itself before it succeeds.
- `theatres/essai-lattaquie.json`, a small test theatre.
- `requirements-fabrique.txt`: the closed, pinned list of runtime dependencies (no test or browser tooling).
- `theatres/syria.json`, `theatres/germanycw.json`, `theatres/caucasus.json`: the three DCS World theatres, with their extents (`zones/`) and the Geofabrik extracts that cover them.

### Changed
- Sea connectivity is computed in row bands: memory at that step no longer grows with the area; outputs are byte-identical.
- `verifier` also checks the output folder's layout, file modes and relative URLs.

### Fixed
- An area whose geometry cannot be built from an OpenStreetMap extract (a broken multipolygon, missing node locations) is skipped and logged instead of stopping the build.

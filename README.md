# dcs-readyroom-basemaps

A basemap factory for DCS World theatres. From an extent, OpenStreetMap extracts and the Copernicus DEM tiles it
produces a **servable folder**: vector tiles and shaded-relief tiles (PMTiles), two MapLibre GL styles (light and
dark) that draw a hand-drawn "paper map" look, a sprite, glyphs, the licence notices and a build manifest.

**What this repository is not:** it does not contain any tile, any OpenStreetMap extract or any elevation tile.
Everything is fetched or supplied when you run a build, and nothing but code, fonts and licences is committed
(see `.gitignore`). There is no `dist/` folder.

## Installation

Python 3.12 and Node 22 (Node is only needed for the test page).

```
python3.12 -m venv venv && . venv/bin/activate
pip install -r requirements.txt        # every version is pinned
npm ci                                 # maplibre-gl and pmtiles, served locally by the test page, never from a CDN
```

No compiled tool outside pip and npm is needed (no tippecanoe, no GDAL command line, no Java).

## The three commands

```
python3.12 -m fabrique extraire-overpass --emprise zones/lattaquie.geojson --sortie lattaquie.osm.gz
python3.12 -m fabrique fabriquer --emprise zones/lattaquie.geojson --osm lattaquie.osm.gz \
        --cache-relief cache/ --sortie out/
python3.12 -m fabrique verifier out/
```

* `extraire-overpass` fetches a small area (refused above 5 000 km2) into an `.osm.gz`. Overpass is queried over
  HTTP only, with a `User-Agent` naming this repository; 502 to 504 answers are retried after 30 to 45 s, five times
  at most. A build never calls Overpass: bigger areas are built from extracts such as Geofabrik's.
* `fabriquer` reads one or more `.osm`, `.osm.gz` or `.osm.pbf` files (sorted: nodes, ways, relations) and downloads
  the Copernicus tiles it needs into `--cache-relief`. An output folder that exists and is not empty is refused and
  nothing is written.
* `verifier` replays the checks on a produced folder: no village anywhere, the licence notices, the colour
  saturation of both styles, and that nothing lies outside the extent. It exits with 0 or 1.

The extent is a GeoJSON file holding a `Polygon` or a `MultiPolygon` in WGS84; nothing is written outside it.
`zones/lattaquie.geojson` is a 50 x 50 km test square.

## The produced folder

| file | content |
|---|---|
| `fond.pmtiles` | vector tiles (MVT), zooms 5 to 14 |
| `relief.pmtiles` | shaded relief, WebP 512 px tiles with alpha (ink on transparent), zooms 5 to 12 |
| `style-clair.json`, `style-sombre.json` | the two themes; **the same tiles** |
| `sprite/` | `papier.json`, `papier.png`, `papier@2x.json`, `papier@2x.png` |
| `glyphs/<font>/<start>-<end>.pbf` | Special Elite and JetBrains Mono, ranges 0-255 to 1024-1279 |
| `LICENCE.txt` | the notices (one paragraph per source present in the manifest) |
| `manifeste.json` | the inputs (path or address, size, sha256), tool versions, settings |

Vector layers: `mer`, `rivage`, `eau`, `cours_eau`, `courbes`, `forets` (tree points, never fills), `localites`,
`lieux` (cities and towns only), `routes`, `rail`, `pistes`, `frontieres`, `grille` (the MGRS grid, cut at UTM zone
boundaries). The hand-drawn wobble of lines and outlines is baked into the geometry, per zoom band
(5-6, 7-8, 9-10, 11-12, 13-14); runways and the grid stay straight. Villages, hamlets, isolated dwellings and
localities are never drawn, at any zoom, and have no name.

Builds are reproducible: the same inputs and the same tool versions give byte-identical tiles, styles, sprite and
glyphs. The manifest carries the only date, in `fabrique_le`, outside the part used to compare two builds.

## Loading a style

The style URLs are relative to the style file (`pmtiles://./fond.pmtiles`, `./sprite/papier`,
`./glyphs/{fontstack}/{range}.pbf`). `web/charger-style.js` is a small dependency-free function that loads a style by
its URL and resolves those paths against it:

```js
const protocole = new pmtiles.Protocol();
maplibregl.addProtocol('pmtiles', protocole.tile);
const style = await chargerStyle('/fonds/lattaquie/style-clair.json');
const carte = new maplibregl.Map({ container: 'carte', style });
```

## Test page and renderer

`web/essai.html` is a local test page. `essais/rendre.py` serves a produced folder (with `Range` support) and
`web/`, opens the page in Chromium with every request outside `127.0.0.1` refused and counted, fits the map to a box,
waits for `idle` and writes a PNG:

```
python3.12 essais/rendre.py out/ zones/lattaquie.geojson 1600x1600 clair vue_clair.png
python3.12 essais/rendre.py out/ carre:35.99,35.43,12 1200x1200 sombre detail_sombre.png
python3.12 essais/rendre.py out/ centre:36.0,35.43,7 1200x800 clair z07_clair.png
```

Chromium is taken from `/opt/pw-browsers` (or `CHROMIUM_PATH`); WebGL runs through SwiftShader.
`essais/comparer.py <A> <B>` compares two produced folders file by file (the manifest by its comparable part).
Run the tests with `python3.12 -m pytest -q`.

## Sources and licences

* Map data © OpenStreetMap contributors, available under the Open Database License (ODbL) v1.0,
  https://www.openstreetmap.org/copyright. The vector tiles are a Produced Work made available under the ODbL.
* Relief produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018
  provided under COPERNICUS by the European Union and ESA; all rights reserved (GLO-90 where GLO-30 is unavailable,
  with the same notice for WorldDEM-90). The exact notices are written into each build's `LICENCE.txt`.
* Fonts, in `polices/` with their full licence texts: Special Elite (Apache License 2.0) and JetBrains Mono (SIL Open
  Font License 1.1).
* The code of this repository is under the MIT License (`LICENSE`).

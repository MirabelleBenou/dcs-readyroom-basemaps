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

## The commands

```
python3.12 -m fabrique extraire-overpass --emprise zones/lattaquie.geojson --sortie lattaquie.osm.gz
python3.12 -m fabrique fabriquer --emprise zones/lattaquie.geojson --osm lattaquie.osm.gz \
        --cache-relief ../cache/ --sortie ../out/ [--bloc-zoom 8]
python3.12 -m fabrique theatre <name> --sortie <folder> --travail <folder> --cache <folder>
python3.12 -m fabrique verifier ../out/
```

* `extraire-overpass` fetches a small area (refused above 5 000 km2) into an `.osm.gz`. Overpass is queried over
  HTTP only, with a `User-Agent` naming this repository; 502 to 504 answers are retried after 30 to 45 s, five times
  at most. A build never calls Overpass: bigger areas are built from extracts such as Geofabrik's.
* `fabriquer` reads one or more `.osm`, `.osm.gz` or `.osm.pbf` files (sorted: nodes, ways, relations) and downloads
  the Copernicus tiles it needs into `--cache-relief`. An output folder that exists and is not empty is refused and
  nothing is written.
  Build outside the working tree (`../out/` above): the output folder is refused if it exists and is not empty,
  and a work folder `<sortie>.travail-*` (the on-disk spatial store, the node index, the elevation pyramid) is
  created next to it and erased at the end, even on error.
* `--bloc-zoom` (7 to 10, default 8) sets the size of the blocks the build is processed in: a block is one tile of
  that zoom and is processed whole in memory, everything else lives on disk. A smaller zoom means bigger blocks
  (more memory, faster); the output is byte-identical whatever the value. Peak memory depends on the block size,
  not on the extent; disk use (work folder) grows with the extent.
* `theatre` builds a whole theatre from its descriptor, unattended: see "Building a theatre" below.
* `verifier` replays the checks on a produced folder: no village anywhere, the licence notices, the colour
  saturation and colour syntax of both styles, that nothing lies outside the extent, that every glyph range the
  styles can ask for exists, and how many names were written or left silent per class. It also checks the layout of
  the folder: exactly `fond.pmtiles`, `relief.pmtiles`, `style-clair.json`, `style-sombre.json`, `sprite/`, `glyphs/`,
  `LICENCE.txt` and `manifeste.json` at the root, files in mode 644, folders in 755, no symbolic link, relative
  addresses only (`pmtiles://./...`, `./...`, no `..` segment) for the sources, the sprite and the glyphs of both styles, and a
  `fabrique.commit` in the manifest. It exits with 0 or 1.

The extent is a GeoJSON file holding a `Polygon` or a `MultiPolygon` in WGS84; nothing is written outside it.
`zones/lattaquie.geojson` is a 50 x 50 km test square.

## Building a theatre

`fabriquer` takes its inputs on the command line. `theatre` is the same build for a whole theatre, meant to be run by a
launcher in a locked-down container with nobody watching: everything it needs is named by a **descriptor**, and
everything it writes goes to three folders you give it.

```
python3.12 -B -m fabrique theatre essai-lattaquie --sortie /run/out/sortie --travail /run/out/travail --cache /var/cache/fonds
```

**The descriptor** is `theatres/<name>.json`, read from the code's own tree. The name matches
`^[a-z0-9][a-z0-9-]{0,31}$` and equals the file name without `.json`. Keys, and no others (an unknown or a missing key
is refused):

* `nom`: the name;
* `emprise`: a path relative to the repository root, under `zones/`, to a GeoJSON extent (as for `fabriquer`);
* `osm`: a non-empty list of `https://` addresses (nothing else is accepted, no credentials, no query string), each
  ending in `.osm.pbf`, `.osm.gz` or `.osm`;
* `bloc_zoom` (optional): 7 to 10, 8 by default.

`theatres/essai-lattaquie.json` is a small test theatre (the 50 x 50 km test square, read from a whole-country extract).

Run it with `python -B` (or `PYTHONDONTWRITEBYTECODE=1`): Python would otherwise try to write `__pycache__` folders into
the code tree, which a locked-down container keeps read-only.

**The three folders.** Before anything is written the command checks, and refuses with a non-zero exit code and a plain
message: the descriptor; that `--sortie` does not exist and its parent does; that `--travail` exists and is empty (and
is on the same file system as the parent of `--sortie`); that `--cache` exists; that the three folders do not overlap
(by real path: `--sortie` is not under `--travail` or `--cache`, and neither of those two is under the other, or the same);
and the commit (see `FABRIQUE_COMMIT` below).

* `--sortie`: appears at the end, by a single `os.replace` from the work folder, complete and verified. Files are in
  mode 644 and folders in 755, whatever the umask; there is no symbolic link.
* `--travail`: everything temporary lives there (the spatial store, the node index, the elevation pyramid, SQLite and
  GDAL temporary files, the folder being built). It is empty when the command ends, on success and on error; the folder
  itself stays.
* `--cache`: kept from one build to the next, and the only thing that is. `relief/` holds the Copernicus tiles (as for
  `fabriquer`); `osm/` holds each extract under `<first 16 hex digits of the sha256 of its address>-<file name of the
  address>`. An extract that is in the cache is used **without touching the network**; one that is not is downloaded
  over HTTPS into a `.part` file of the same folder: status 200, size equal to
  `Content-Length` when the header is there, a header pyosmium can read, then `os.replace`. Any error removes the
  `.part` file and fails the build. **To refresh an extract, remove it from the cache.**

Nothing is written outside these three folders and `/tmp`; the network is used for HTTPS only, towards the addresses of
the descriptor and the Copernicus buckets, never Overpass. The extracts **and the elevation tiles** are fetched by the
same opener, which follows no redirection to another host (or port) than the one requested, nor out of HTTPS; a
redirection to the same host over HTTPS is followed.

**`FABRIQUE_COMMIT`.** When the environment variable is set it must be 40 lowercase hexadecimal digits (otherwise the
build is refused before anything is written); the manifest then carries `fabrique: {"commit": <value>, "modifie":
false}` and Git is never called, so a container needs neither Git nor a `.git` folder. When it is absent the manifest
asks Git, as `fabriquer` does, and `theatre` refuses before writing anything if Git does not give a 40-digit commit
either; the final check requires the manifest to carry such a `fabrique.commit`. (`verifier` alone still accepts
`"inconnu"`, which a local `fabriquer` writes from an archive without `.git`, and logs a warning.)

**Progress.** Lines `progression : <step> <n>/<total>` go to the standard output at every change of step, at every
block (and every row band of the elevation and sea steps), and, whatever happens, at most 20 s after the previous one:
a heartbeat repeats the step and the elapsed time. They carry no address and no secret. The rest of the log (`[ 12.3 s]
step`) is unchanged.

**Exit code.** 0 if and only if the output folder is complete, passes the checks of `verifier` and is in place. A work folder that is not empty
after the clean-up is a failure too, even after a successful placement. Any
other case: non-zero (2 for a refusal or a failure, with `erreur : <message>` on the standard error; 143 when the
process is terminated, the work folder being emptied first) and `--sortie` absent.

**Dependencies.** `requirements-fabrique.txt` is the closed list of runtime dependencies (every transitive one, each
pinned with `==` at the version of `requirements.txt`; no browser, no test tool). It installs in a fresh Python 3.12
virtual environment, x86_64, without any system package:

```
python3.12 -m venv /opt/fabrique && /opt/fabrique/bin/pip install --no-deps --only-binary :all: -r requirements-fabrique.txt
```

`requirements.txt` stays the development environment.

**Resources.** The work folder holds the elevation pyramid and the on-disk spatial store: about 3.1 GB for a 90 000 km2
extent (3 x 3 degrees at 35 N), growing linearly with the area (plan for roughly 26 GB for 750 000 km2); that build
takes about 17 minutes on two cores and peaks at about 600 MB. GDAL's block cache is capped at 64 MB for the run
(set `GDAL_CACHEMAX` to choose another value), so that memory does not depend on the machine's.

**Memory.** The step whose memory grew with the area, the sea (which pixels of the zoom 10 grid are sea: low
pixels connected to the border of the extent and holding a seed), runs in row bands: the grids are packed one bit per
pixel and the labelling is done band by band, so the output is byte-identical to the previous whole-grid computation.

## The produced folder

| file | content |
|---|---|
| `fond.pmtiles` | vector tiles (MVT), zooms 5 to 14 |
| `relief.pmtiles` | shaded relief, WebP 512 px tiles with alpha (ink on transparent), zooms 5 to 12 |
| `style-clair.json`, `style-sombre.json` | the two themes; **the same tiles** |
| `sprite/` | `papier.json`, `papier.png`, `papier@2x.json`, `papier@2x.png` |
| `glyphs/<fontstack>/<start>-<end>.pbf` | three stacks: `Special Elite,JetBrains Mono` (names; a glyph comes from Special Elite when it has it, else from JetBrains Mono), `JetBrains Mono` (grid) and `Special Elite`; ranges 0-255 to 1024-1279 plus the blocks that hold glyphs |
| `LICENCE.txt` | the notices (one paragraph per source present in the manifest) |
| `manifeste.json` | the inputs (path or address, size, sha256), tool versions, settings |

Vector layers: `mer`, `rivage`, `eau`, `cours_eau`, `courbes`, `forets` (tree points, never fills), `localites`,
`lieux` (cities and towns only), `routes`, `rail`, `pistes`, `frontieres`, `grille` (the MGRS grid, cut at UTM zone
boundaries). The hand-drawn wobble of lines and outlines is baked into the geometry, per zoom band
(5-6, 7-8, 9-10, 11-12, 13-14); runways and the grid stay straight. Villages, hamlets, isolated dwellings and
localities are never drawn, at any zoom, and have no name.

Names: the first of `name:en`, `int_name`, `name`. A name with a character that neither font holds is not written
(the OSM id goes to the build log) rather than drawn with missing letters; `verifier` counts the names written and
left silent per class.

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
waits for `idle` and writes a PNG; it also counts the 404 answers (the exit code is 0 only with no refused request,
no 404 and a non-empty image):

```
python3.12 essais/rendre.py ../out/ zones/lattaquie.geojson 1600x1600 clair vue_clair.png
python3.12 essais/rendre.py ../out/ carre:35.99,35.43,12 1200x1200 sombre detail_sombre.png
python3.12 essais/rendre.py ../out/ centre:36.0,35.43,7 1200x800 clair z07_clair.png
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

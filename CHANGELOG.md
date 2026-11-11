# Changelog

All notable changes to this project are documented here. Versions follow semantic versioning; a release is cut on request.

## Unreleased

### Added
- `fabrique`: builds a paper-style basemap for any area from OpenStreetMap extracts and the Copernicus DEM (GLO-30, with GLO-90 where GLO-30 is unavailable): vector tiles (`fond.pmtiles`, zooms 5–14), shaded relief (`relief.pmtiles`, zooms 5–12), light and dark styles, sprite, glyphs, licence notices and a build manifest.
- Hand-drawn look baked into the geometry, per zoom band; true UTM/MGRS grid across zone boundaries.
- No village names or village markers at any zoom; towns and cities only.
- Reproducible builds: the same inputs and tools give byte-identical outputs.
- Bounded memory: the build works by blocks (`--bloc-zoom`, 7 to 10) with an on-disk spatial store; the output is byte-identical whatever the block size.
- Names: first of `name:en`, `int_name`, `name`; names that a font cannot draw are left silent; composed glyph stack `Special Elite,JetBrains Mono`.
- `extraire-overpass` to fetch a small test area; `verifier` to check a build; a local test page and renderer.

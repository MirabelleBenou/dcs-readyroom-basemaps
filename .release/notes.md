### Fixed
- A border added by a theatre rule (`frontieres_ajoutees`) is now recognised by the relations it belongs to, even when the way itself carries no `boundary` tag, as is common in OpenStreetMap; a way tagged `maritime=yes` is still left out.

### Changed
- A `frontieres_ajoutees` rule whose relations share no way between `a` and `b` is refused as soon as the relations are read, before the long first pass.
- The log gives one line per OpenStreetMap extract read, with its rank and duration.
- A Copernicus DEM tile is tried five times, 35 s apart, on a network error; a 404 still means no tile, and any other HTTP status still stops the build.

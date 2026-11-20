### Fixed
- A GEOS error while rounding or merging the geometries of a tile no longer stops a build: sea pieces that are not valid are made valid before they are merged; a failed merge falls back to a merge without the grid, then to the separate pieces; a geometry that cannot be rounded is made valid and rounded again, then left out. Without any such error, the tiles are unchanged, byte for byte.

### Changed
- The build log counts these fallbacks in one line and names the first tiles; there is no line when there are none.
- An error while encoding a tile now names the tile (z/x/y) and the layer.

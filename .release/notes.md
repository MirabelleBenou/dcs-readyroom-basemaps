Base Maps BM-V0.2.5.

**Release tags start with BM-V**
- From the next release on, tags read `BM-V<major>.<minor>.<patch>`; the header of this changelog says what each number means.

**Release pages explain the version numbers**
- Each release page ends with a "Version numbers" paragraph, the same as the header of this changelog; a release whose paragraph is missing or different is not published.

**Project banner**
- The README opens with the Base Maps banner of the Squadron Ready Room family. The maps do not change.

**GermanyCW: more motorways opened after 1990 left out**
- Motorway sections opened after 1990 in West Germany, Poland and the Czech Republic are no longer drawn on the GermanyCW map, as was already done for former East Germany.

**Build log: reading time of each OpenStreetMap extract**
- The check at the end of a build prints, in one line, how long each OpenStreetMap extract took to read; `manifeste.json` keeps the same figures. The maps do not change.

## Version numbers

Each release is a GitHub release tagged `BM-V<major>.<minor>.<patch>`; releases up to 0.2.4 were tagged `v<major>.<minor>.<patch>`:
- **major**: a redesign of the map style, or a change to the produced folder that breaks a map reading it;
- **minor**: a new theatre, layer or command, or a change visible on every map;
- **patch**: fixes.

A built basemap names the exact commit of the factory in its `manifeste.json`; there is no build number.

### Fixed
- A theatre build now follows the redirection of an OpenStreetMap extract to a mirror on another host (HTTPS only), and keeps the file only if it matches the MD5 checksum that the original host publishes next to the extract (`<address>.md5`); the build stops otherwise. Elevation tiles still follow no redirection to another host.

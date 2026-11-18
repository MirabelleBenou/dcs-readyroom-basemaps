### Changed
- An OpenStreetMap extract served by a mirror on another host is no longer checked against an MD5 checksum, since the original host publishes none for redirected extracts; the checks on status, size and header remain, and the redirection must still stay on HTTPS, port 443, without credentials.

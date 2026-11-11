"""Un petit serveur local qui honore `Range` (le pmtiles du navigateur en a besoin) et ne sert que trois dossiers."""
import http.server
import os
import re
import threading
import urllib.parse

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css",
         ".json": "application/json", ".png": "image/png", ".pbf": "application/x-protobuf",
         ".pmtiles": "application/octet-stream", ".txt": "text/plain; charset=utf-8"}


def faire_gestionnaire(dossier_produit):
    cartes = {
        "/fond/": os.path.abspath(dossier_produit),
        "/web/": os.path.join(RACINE, "web"),
        "/vendor/maplibre-gl/": os.path.join(RACINE, "node_modules", "maplibre-gl", "dist"),
        "/vendor/pmtiles/": os.path.join(RACINE, "node_modules", "pmtiles", "dist"),
    }

    class Gestionnaire(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        requetes = []

        def log_message(self, *a):
            pass

        def _chemin(self):
            p = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
            for prefixe, base in cartes.items():
                if p.startswith(prefixe):
                    rel = p[len(prefixe):]
                    f = os.path.abspath(os.path.join(base, rel))
                    if f == base or f.startswith(base + os.sep):
                        return f
            return None

        def _servir(self, tete):
            f = self._chemin()
            if f is None or not os.path.isfile(f):
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            taille = os.path.getsize(f)
            ext = os.path.splitext(f)[1]
            debut, fin, code = 0, taille - 1, 200
            m = re.match(r"bytes=(\d*)-(\d*)$", self.headers.get("Range", ""))
            if m and (m.group(1) or m.group(2)):
                if m.group(1):
                    debut = int(m.group(1))
                    fin = min(int(m.group(2)), taille - 1) if m.group(2) else taille - 1
                else:
                    debut = max(0, taille - int(m.group(2)))
                if debut > fin or debut >= taille:
                    self.send_response(416)
                    self.send_header("Content-Range", "bytes */%d" % taille)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                code = 206
            self.send_response(code)
            self.send_header("Content-Type", TYPES.get(ext, "application/octet-stream"))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(fin - debut + 1))
            if code == 206:
                self.send_header("Content-Range", "bytes %d-%d/%d" % (debut, fin, taille))
            self.end_headers()
            if not tete:
                with open(f, "rb") as fh:
                    fh.seek(debut)
                    restant = fin - debut + 1
                    while restant > 0:
                        bloc = fh.read(min(1 << 20, restant))
                        if not bloc:
                            break
                        self.wfile.write(bloc)
                        restant -= len(bloc)

        def do_GET(self):
            self._servir(False)

        def do_HEAD(self):
            self._servir(True)

    return Gestionnaire


def demarrer(dossier_produit, port=0):
    """Rend (serveur, port) ; le serveur tourne dans un fil, à arrêter par serveur.shutdown()."""
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), faire_gestionnaire(dossier_produit))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, srv.server_address[1]

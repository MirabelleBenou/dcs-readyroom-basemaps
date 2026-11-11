"""Rend une vue du dossier produit par MapLibre dans Chromium, sans réseau extérieur.

Usage : python3.12 essais/rendre.py <dossier> <cadre> <largeur>x<hauteur> <clair|sombre> <image.png>
  <cadre> : un fichier GeoJSON (l'emprise), "ouest,sud,est,nord", "carre:LON,LAT,KM" (un carré de KM km de côté)
            ou "centre:LON,LAT,ZOOM".
Toute requête hors de 127.0.0.1 est refusée et comptée ; le compte est affiché et rendu en code de sortie (0 = zéro).
"""
import glob
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from playwright.sync_api import sync_playwright  # noqa: E402

import serveur  # noqa: E402

ARGS_CHROMIUM = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist",
                 "--enable-webgl", "--no-sandbox", "--disable-dev-shm-usage", "--disable-background-networking",
                 "--disable-component-update", "--disable-sync", "--no-first-run", "--disable-features=OptimizationHints,MediaRouter"]


def trouver_chromium():
    env = os.environ.get("CHROMIUM_PATH")
    if env:
        return env
    base = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")
    c = sorted(glob.glob(os.path.join(base, "chromium-*", "chrome-linux*", "chrome")))
    if not c:
        raise SystemExit("Chromium introuvable sous %s (variable CHROMIUM_PATH)" % base)
    return c[-1]


def lire_cadre(cadre):
    """Rend (options de demarrer sans le style)."""
    if cadre.startswith("centre:"):
        lon, lat, z = (float(v) for v in cadre[7:].split(","))
        return {"centre": [lon, lat], "zoom": z}
    if cadre.startswith("carre:"):
        lon, lat, km = (float(v) for v in cadre[6:].split(","))
        dlat = km * 500.0 / 111132.0
        dlon = km * 500.0 / (111320.0 * math.cos(math.radians(lat)))
        return {"boite": [lon - dlon, lat - dlat, lon + dlon, lat + dlat]}
    if os.path.isfile(cadre):
        from fabrique.emprise import lire_emprise
        return {"boite": list(lire_emprise(cadre).bounds)}
    v = [float(x) for x in cadre.split(",")]
    if len(v) != 4:
        raise SystemExit("cadre illisible : %r" % cadre)
    return {"boite": v}


def rendre(dossier, cadre, taille, theme, image, chromium=None):
    """Rend l'image ; rend le nombre de requêtes refusées hors de 127.0.0.1."""
    largeur, hauteur = (int(v) for v in taille.lower().split("x"))
    srv, port = serveur.demarrer(dossier)
    refusees = []
    try:
        with sync_playwright() as p:
            nav = p.chromium.launch(executable_path=chromium or trouver_chromium(), headless=True, args=ARGS_CHROMIUM)
            ctx = nav.new_context(viewport={"width": largeur, "height": hauteur}, device_scale_factor=1)
            page = ctx.new_page()

            def garde(route):
                u = route.request.url
                if u.startswith("http://127.0.0.1:%d/" % port):
                    route.continue_()
                else:
                    refusees.append(u)
                    route.abort()

            page.route("**/*", garde)
            msgs = []
            page.on("console", lambda m: msgs.append("%s: %s" % (m.type, m.text)))
            page.on("pageerror", lambda e: msgs.append("pageerror: %s" % e))
            page.goto("http://127.0.0.1:%d/web/essai.html" % port)
            try:
                page.wait_for_function("window.__pret === true", timeout=60000)
            except Exception:
                print("la page n'a pas démarré ; console :", *msgs, "requêtes refusées :", *refusees, sep="\n  ")
                raise
            opt = dict(lire_cadre(cadre), style="/fond/style-%s.json" % theme)
            res = page.evaluate("(o) => window.demarrer(o)", opt)
            page.wait_for_timeout(300)
            page.screenshot(path=image, clip={"x": 0, "y": 0, "width": largeur, "height": hauteur})
            nav.close()
    finally:
        srv.shutdown()
        srv.server_close()
    return refusees, res, msgs


def verifier_image(chemin, minimum=200):
    """Le nombre de couleurs distinctes de l'image (un rendu vide en a moins de 200)."""
    from PIL import Image
    im = Image.open(chemin).convert("RGB")
    return len(im.getcolors(maxcolors=im.size[0] * im.size[1]) or [])


def principal(argv=None):
    a = argv if argv is not None else sys.argv[1:]
    if len(a) != 5 or a[3] not in ("clair", "sombre"):
        print(__doc__)
        return 2
    refusees, res, msgs = rendre(*a)
    n = verifier_image(a[4])
    print("image %s : %d couleurs distinctes ; vue %s" % (a[4], n, json.dumps(res)))
    print("requêtes refusées hors de 127.0.0.1 : %d" % len(refusees))
    for u in refusees:
        print("  refusée : %s" % u)
    for m in msgs:
        if not m.startswith("log:") and not m.startswith("debug:"):
            print("  console : %s" % m)
    return 0 if not refusees and n > 200 else 1


if __name__ == "__main__":
    sys.exit(principal())

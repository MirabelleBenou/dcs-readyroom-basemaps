"""manifeste.json : les entrées, les outils, les réglages. `fabrique_le` est la seule donnée datée."""
import datetime
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess

from . import bandes, derive, osm, terrain
from .sources import sha256_fichier

DEPENDANCES = ("numpy", "scipy", "shapely", "pyproj", "rasterio", "osmium", "contourpy", "mapbox-vector-tile",
               "pmtiles", "Pillow", "fonttools", "brotli", "protobuf", "pyclipper")


def commit_fabrique(racine):
    """Le commit du code de la fabrique (et s'il y a des modifications non validées)."""
    try:
        h = subprocess.run(["git", "-C", racine, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        sale = subprocess.run(["git", "-C", racine, "status", "--porcelain", "--untracked-files=no"],
                              capture_output=True, text=True, check=True).stdout.strip() != ""
        return {"commit": h, "modifie": sale}
    except (OSError, subprocess.CalledProcessError):
        return {"commit": "inconnu", "modifie": None}


def versions():
    out = {}
    for n in DEPENDANCES:
        try:
            out[n] = importlib.metadata.version(n)
        except importlib.metadata.PackageNotFoundError:
            out[n] = None
    return out


def bibliotheques():
    """Les versions des bibliothèques natives dont dépendent les octets produits : zlib (gzip des tuiles), libwebp (relief),
    FreeType (glyphes), GEOS (géométrie), GDAL (dalles), PROJ (grille), et le runtime SQLite du magasin."""
    import sqlite3
    import zlib

    import pyproj
    import rasterio
    import shapely
    from PIL import features
    return {"zlib": zlib.ZLIB_RUNTIME_VERSION, "libwebp": features.version("webp"),
            "freetype": features.version("freetype2"), "geos": shapely.geos_version_string,
            "gdal": rasterio.__gdal_version__, "proj": pyproj.proj_version_str, "sqlite": sqlite3.sqlite_version}


def entree_fichier(chemin, nom=None):
    return {"fichier": nom or os.path.basename(chemin), "taille": os.path.getsize(chemin), "sha256": sha256_fichier(chemin)}


def reglages():
    return {
        "bandes": [list(b) for b in bandes.BANDES],
        "zooms_fond": [bandes.ZOOM_MIN_FOND, bandes.ZOOM_MAX_FOND],
        "zooms_relief": [bandes.ZOOM_MIN_RELIEF, bandes.ZOOM_MAX_RELIEF],
        "tremblement": {"amplitude_px": bandes.AMPLITUDE_PX, "longueur_onde_px": bandes.ONDE_PX},
        "tolerance_px": bandes.TOLERANCE_PX,
        "arbres_espacement_px": bandes.ESPACE_ARBRES_PX,
        "seuils": {"localite_m2": osm.SEUIL_LOCALITE_M2, "ruisseau_m": osm.SEUIL_RUISSEAU_M,
                   "eau_px2": bandes.SEUIL_EAU_PX2, "ligne_px": bandes.SEUIL_LIGNE_PX},
        "mer": {"seuil_m": terrain.SEUIL_MER, "semence_m": terrain.SEMENCE_MER, "grille_globale_zoom": terrain.Z_MER},
        "courbes": {"sigma_px": terrain.SIGMA_COURBES},
        "cellules": {"%d-%d" % b: {"zoom_cellule": z, "niveau_relief": n} for b, (z, n) in derive.PARAMS.items()},
        "tolerance_apres_px": derive.TOLERANCE_APRES_PX,
        "estompage": dict(terrain.ESTOMPAGE),
        "tuile_relief_px": 512,
        "etendue_mvt": 4096,
    }


def construire(racine, emprise, osm_entrees, relief, polices, nom_emprise, noms=None, execution=None):
    """Rend le manifeste (dict). `emprise` : {fichier, sha256, taille, geometrie}. `noms` : les noms écrits et tus par classe
    (le résultat de la lecture). `execution` : ce qui dépend de la machine ou du découpage (zoom pivot, durée, mémoire) :
    hors de la partie comparable."""
    return {
        "fabrique_le": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fabrique": commit_fabrique(racine),
        "python": platform.python_version(),
        "dependances": versions(),
        "bibliotheques": bibliotheques(),
        "reglages": reglages(),
        "entrees": {
            "emprise": emprise,
            "osm": osm_entrees,
            "relief": [{k: v for k, v in e.items() if k != "fichier"} for e in relief],
            "polices": polices,
        },
        "noms": noms if noms is not None else {},
        "execution": execution if execution is not None else {},
    }


def partie_comparable(m):
    """Tout, sauf la date de fabrication et ce qui tient à l'exécution (le zoom pivot des blocs, la durée, la mémoire)."""
    return {k: v for k, v in m.items() if k not in ("fabrique_le", "execution")}


def sha256_comparable(m):
    blob = json.dumps(partie_comparable(m), sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def ecrire(chemin, m):
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(m, f, indent=1, sort_keys=True, ensure_ascii=False)
        f.write("\n")

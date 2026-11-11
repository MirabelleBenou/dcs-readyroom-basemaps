"""Les dalles du relief Copernicus : sélection (GLO-30, repli GLO-90, mer), cache, empreintes."""
import hashlib
import math
import os
import shutil
import urllib.error
import urllib.request

import rasterio

GLO30 = "glo30"
GLO90 = "glo90"
ABSENTE = "absente"

HOTES = {GLO30: "copernicus-dem-30m", GLO90: "copernicus-dem-90m"}
CODES = {GLO30: 10, GLO90: 30}
USER_AGENT = "dcs-readyroom-basemaps (+https://github.com/MirabelleBenou/dcs-readyroom-basemaps)"


class SourceErreur(RuntimeError):
    pass


def nom_dalle(lat, lon):
    """Nom d'une dalle d'un degré, par son coin sud-ouest entier : N35_00_E035_00."""
    return "%s%02d_00_%s%03d_00" % ("N" if lat >= 0 else "S", abs(lat), "E" if lon >= 0 else "W", abs(lon))


def url_dalle(lat, lon, source):
    base = "Copernicus_DSM_COG_%d_%s_DEM" % (CODES[source], nom_dalle(lat, lon))
    return "https://%s.s3.amazonaws.com/%s/%s.tif" % (HOTES[source], base, base)


def dalles_necessaires(boite):
    """Les dalles d'un degré qui recouvrent la boîte (lon_min, lat_min, lon_max, lat_max)."""
    lon0, lat0, lon1, lat1 = boite
    return [(lat, lon) for lat in range(math.floor(lat0), math.ceil(lat1))
            for lon in range(math.floor(lon0), math.ceil(lon1))]


def choisir_dalles(boite, glo30_disponible, glo90_disponible):
    """La sélection, sans réseau : `glo30_disponible(lat, lon)` et `glo90_disponible(lat, lon)` rendent un booléen.

    Chaque dalle vient du GLO-30 ; à défaut du GLO-90 ; à défaut elle est absente (la mer : altitude 0).
    Rend une liste de dicts {dalle, lat, lon, source, url}, dans l'ordre (lat, lon).
    """
    out = []
    for lat, lon in sorted(dalles_necessaires(boite)):
        if glo30_disponible(lat, lon):
            src = GLO30
        elif glo90_disponible(lat, lon):
            src = GLO90
        else:
            src = ABSENTE
        out.append({"dalle": nom_dalle(lat, lon), "lat": lat, "lon": lon, "source": src,
                    "url": None if src == ABSENTE else url_dalle(lat, lon, src)})
    return out


def sha256_fichier(chemin):
    h = hashlib.sha256()
    with open(chemin, "rb") as f:
        for bloc in iter(lambda: f.read(1 << 20), b""):
            h.update(bloc)
    return h.hexdigest()


# ---------------------------------------------------------------------- le cache et le réseau
def chemin_cache(cache, lat, lon, source):
    return os.path.join(cache, os.path.basename(url_dalle(lat, lon, source)))


def marque_absence(cache, lat, lon, source):
    """Le fichier qui dit « le seau n'a pas cette dalle (404) » : un cache complet n'a plus besoin du réseau."""
    return chemin_cache(cache, lat, lon, source) + ".absente"


def _telecharger(url, destination, ouvrir=urllib.request.urlopen, tentatives=3):
    """Télécharge url. Rend True ; False pour un 404 (la dalle n'existe pas). Tout autre code HTTP est une erreur
    (`SourceErreur`) qui arrête la fabrication. Le fichier ne prend sa place qu'entier : taille égale à Content-Length,
    ouvert par rasterio ; le `.part` s'efface sur toute erreur."""
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    tmp = destination + ".part"
    derniere = None
    for _ in range(tentatives):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with ouvrir(req, timeout=300) as r:
                code = getattr(r, "status", 200)
                if code != 200:
                    raise SourceErreur("%s : HTTP %s" % (url, code))
                attendu = r.headers.get("Content-Length")
                with open(tmp, "wb") as f:
                    shutil.copyfileobj(r, f, 1 << 20)
            if attendu is not None and os.path.getsize(tmp) != int(attendu):
                raise OSError("réponse tronquée : %d octets sur %s" % (os.path.getsize(tmp), attendu))
            with rasterio.open(tmp) as ds:
                if ds.count < 1 or ds.width < 1:
                    raise OSError("dalle illisible")
            os.replace(tmp, destination)
            return True
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return False
            raise SourceErreur("le seau refuse %s (HTTP %s)" % (url, e.code))
        except SourceErreur:
            raise
        except (urllib.error.URLError, OSError, rasterio.errors.RasterioError) as e:
            derniere = e
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    raise SourceErreur("téléchargement impossible : %s (%r)" % (url, derniere))


def _dalle(cache, lat, lon, ouvrir):
    """Le fichier d'une dalle et sa source : le cache d'abord (aucun réseau s'il suffit), puis le seau GLO-30, puis GLO-90
    (404 seul), puis la mer (altitude 0). Rend (source, fichier ou None)."""
    for src in (GLO30, GLO90):
        local = chemin_cache(cache, lat, lon, src)
        if os.path.isfile(local):
            return src, local
        if os.path.isfile(marque_absence(cache, lat, lon, src)):
            continue
        if _telecharger(url_dalle(lat, lon, src), local, ouvrir):
            return src, local
        open(marque_absence(cache, lat, lon, src), "w").close()
    return ABSENTE, None


def preparer_dalles(boite, cache, ouvrir=urllib.request.urlopen):
    """Choisit les dalles et les met en cache ; rend les entrées du manifeste (une par dalle). Une fabrication au cache
    complet ne touche pas le réseau."""
    os.makedirs(cache, exist_ok=True)
    entrees = []
    for lat, lon in sorted(dalles_necessaires(boite)):
        src, local = _dalle(cache, lat, lon, ouvrir)
        e = {"dalle": nom_dalle(lat, lon), "lat": lat, "lon": lon, "source": src,
             "adresse": None if src == ABSENTE else url_dalle(lat, lon, src)}
        if local:
            e["fichier"] = local                      # hors manifeste (propre au cache)
            e["taille"] = os.path.getsize(local)
            e["sha256"] = sha256_fichier(local)
        entrees.append(e)
    return entrees

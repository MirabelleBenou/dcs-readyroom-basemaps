import io
import os
import urllib.error

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from fabrique import sources


def test_nom_et_url_des_dalles():
    assert sources.nom_dalle(35, 35) == "N35_00_E035_00"
    assert sources.nom_dalle(-1, -2) == "S01_00_W002_00"
    assert sources.url_dalle(35, 36, sources.GLO30) == (
        "https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N35_00_E036_00_DEM/"
        "Copernicus_DSM_COG_10_N35_00_E036_00_DEM.tif")
    assert sources.url_dalle(40, 44, sources.GLO90).startswith(
        "https://copernicus-dem-90m.s3.amazonaws.com/Copernicus_DSM_COG_30_N40_00_E044_00_DEM/")


def test_dalles_necessaires_sans_debordement_a_la_borne_entiere():
    assert sources.dalles_necessaires((35.2, 35.2, 36.2, 35.7)) == [(35, 35), (35, 36)]
    assert sources.dalles_necessaires((35.0, 35.0, 36.0, 36.0)) == [(35, 35)]


def test_selection_glo30_repli_glo90_et_mer():
    """Sans réseau : une liste de dalles simulée."""
    glo30 = {(35, 35), (35, 36)}
    glo90 = {(40, 44)}
    sel = sources.choisir_dalles((35.2, 35.2, 36.2, 35.7), lambda la, lo: (la, lo) in glo30, lambda la, lo: (la, lo) in glo90)
    assert [(d["dalle"], d["source"]) for d in sel] == [("N35_00_E035_00", "glo30"), ("N35_00_E036_00", "glo30")]
    # l'Arménie et l'Azerbaïdjan : absentes du GLO-30, prises au GLO-90 ; la mer : absente des deux
    sel = sources.choisir_dalles((43.5, 39.5, 45.5, 40.5), lambda la, lo: False, lambda la, lo: (la, lo) in glo90)
    sources_ = {d["dalle"]: d["source"] for d in sel}
    assert sources_["N39_00_E043_00"] == "absente"
    assert sources_["N40_00_E044_00"] == "glo90"
    assert next(d for d in sel if d["source"] == "glo90")["url"].startswith("https://copernicus-dem-90m")
    assert all(d["url"] is None for d in sel if d["source"] == "absente")


def tif_octets(lat=35, lon=35, n=8):
    with rasterio.MemoryFile() as mf:
        with mf.open(driver="GTiff", height=n, width=n, count=1, dtype="float32", crs="EPSG:4326",
                     transform=from_origin(lon, lat + 1, 1.0 / n, 1.0 / n)) as dst:
            dst.write(np.full((n, n), 12.0, dtype="float32"), 1)
        return mf.read()


class Reponse:
    def __init__(self, data, code=200, longueur=None):
        self.f = io.BytesIO(data)
        self.status = code
        self.headers = {"Content-Length": str(len(data) if longueur is None else longueur)}

    def read(self, *a):
        return self.f.read(*a)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def serveur(reponses, appels):
    """Un `urlopen` simulé : `reponses[url_contient]` est (code, octets) ou une exception ; les appels sont notés."""
    def ouvrir(req, timeout=None):
        appels.append(req.full_url)
        for cle, r in reponses.items():
            if cle in req.full_url:
                if isinstance(r, Exception):
                    raise r
                if r[0] != 200:
                    raise urllib.error.HTTPError(req.full_url, r[0], "x", {}, None)
                return Reponse(r[1], longueur=r[2] if len(r) > 2 else None)
        raise AssertionError("URL inattendue : " + req.full_url)
    return ouvrir


def test_cache_complet_ne_touche_pas_le_reseau(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    for lat, lon in ((35, 35), (35, 36)):
        (cache / os.path.basename(sources.url_dalle(lat, lon, sources.GLO30))).write_bytes(tif_octets(lat, lon))
    appels = []
    entrees = sources.preparer_dalles((35.2, 35.2, 36.2, 35.7), str(cache), ouvrir=serveur({}, appels))
    assert appels == []
    assert [(e["dalle"], e["source"]) for e in entrees] == [("N35_00_E035_00", "glo30"), ("N35_00_E036_00", "glo30")]
    assert all(e["sha256"] and e["taille"] > 0 for e in entrees)


def test_404_seul_vaut_dalle_absente_puis_le_cache_se_souvient(tmp_path):
    cache = str(tmp_path / "cache")
    appels = []
    srv = serveur({"copernicus-dem-30m": (404, b""), "copernicus-dem-90m": (404, b"")}, appels)
    e = sources.preparer_dalles((35.2, 35.2, 35.8, 35.7), cache, ouvrir=srv)
    assert e[0]["source"] == "absente" and e[0]["adresse"] is None and "fichier" not in e[0]
    assert len(appels) == 2                                       # GLO-30 puis GLO-90, pas plus
    assert sources.preparer_dalles((35.2, 35.2, 35.8, 35.7), cache, ouvrir=serveur({}, [])) == e


def test_repli_glo90_quand_glo30_repond_404(tmp_path):
    cache = str(tmp_path / "cache")
    appels = []
    srv = serveur({"copernicus-dem-30m": (404, b""), "copernicus-dem-90m": (200, tif_octets())}, appels)
    e = sources.preparer_dalles((35.2, 35.2, 35.8, 35.7), cache, ouvrir=srv)
    assert e[0]["source"] == "glo90" and "copernicus-dem-90m" in e[0]["adresse"]


def test_tout_autre_code_est_une_erreur_et_ne_laisse_rien(tmp_path):
    cache = tmp_path / "cache"
    for code in (403, 500, 429):
        appels = []
        srv = serveur({"copernicus-dem-30m": (code, b"")}, appels)
        with pytest.raises(sources.SourceErreur, match=str(code)):
            sources.preparer_dalles((35.2, 35.2, 35.8, 35.7), str(cache), ouvrir=srv)
        assert len(appels) == 1                                   # pas de repli, pas de réessai
        assert not [f for f in os.listdir(cache)]                 # ni dalle, ni marque d'absence, ni .part


def test_dalle_tronquee_ou_illisible_refusee_et_part_efface(tmp_path):
    cache = tmp_path / "cache"
    data = tif_octets()
    tronque = serveur({"copernicus-dem-30m": (200, data[:len(data) // 2], len(data))}, [])
    with pytest.raises(sources.SourceErreur):
        sources.preparer_dalles((35.2, 35.2, 35.8, 35.7), str(cache), ouvrir=tronque)
    assert os.listdir(cache) == []
    pas_une_dalle = serveur({"copernicus-dem-30m": (200, b"ce n'est pas un GeoTIFF")}, [])
    with pytest.raises(sources.SourceErreur):
        sources.preparer_dalles((35.2, 35.2, 35.8, 35.7), str(cache), ouvrir=pas_une_dalle)
    assert os.listdir(cache) == []


def test_dalle_absente_donne_altitude_zero(tmp_path):
    from fabrique import terrain
    g = terrain.Grille((3.9e6, 4.2e6, 3.91e6, 4.21e6))
    src = terrain.SourceDEM([{"dalle": "N35_00_E035_00", "lat": 35, "lon": 35, "source": "absente", "adresse": None}])
    assert float(np.abs(src.lignes(g, 0, 64)).max()) == 0.0


def test_echantillonnage_independant_de_la_fenetre(tmp_path):
    """Un pixel ne dépend que de ses coordonnées : les mêmes lignes lues d'un coup ou par morceaux sont identiques."""
    from fabrique import terrain
    chemin = str(tmp_path / "d.tif")
    n = 90
    lon, lat = 35, 35
    z = (np.add.outer(np.arange(n) * 3.0, np.arange(n) * 7.0)).astype("float32")
    with rasterio.open(chemin, "w", driver="GTiff", height=n, width=n, count=1, dtype="float32", crs="EPSG:4326",
                       transform=from_origin(lon, lat + 1, 1.0 / n, 1.0 / n)) as dst:
        dst.write(z, 1)
    e = [{"dalle": "N35_00_E035_00", "lat": 35, "lon": 35, "source": "glo30", "fichier": chemin}]
    from fabrique import merc
    x, y = merc.lonlat_vers_merc(35.5, 35.5)
    g = terrain.Grille((float(x) - 3000, float(y) - 3000, float(x) + 3000, float(y) + 3000), marge_blocs=0)
    src = terrain.SourceDEM(e)
    tout = src.lignes(g, 0, g.haut)
    morceaux = np.vstack([src.lignes(g, i, min(g.haut, i + 40)) for i in range(0, g.haut, 40)])
    assert np.array_equal(tout, morceaux)
    assert tout.min() > 0


def test_dalle_decalee_aux_bords_de_la_couverture(tmp_path):
    """Les dalles Copernicus ont leurs bords décalés de l'entier (d'un demi-pixel ; ici d'un pixel et demi pour que le cas
    soit net) : une ligne ou une colonne au-delà du dernier pixel de la dalle ne doit pas donner une fenêtre vide, mais
    répéter le bord."""
    from fabrique import merc, terrain
    chemin = str(tmp_path / "d.tif")
    n = 90
    pas = 1.0 / n
    z = np.full((n, n), 50.0, dtype="float32")
    with rasterio.open(chemin, "w", driver="GTiff", height=n, width=n, count=1, dtype="float32", crs="EPSG:4326",
                       transform=from_origin(35 - 1.5 * pas, 36 + 1.5 * pas, pas, pas)) as dst:
        dst.write(z, 1)
    e = [{"dalle": "N35_00_E035_00", "lat": 35, "lon": 35, "source": "glo30", "fichier": chemin}]
    x0, y0 = merc.lonlat_vers_merc(35.0, 35.0)
    # une grille entièrement au sud de la dalle : toutes ses lignes sont ramenées à 35°, donc au-delà du dernier pixel
    g = terrain.Grille((float(x0) + 2000, float(y0) - 3000, float(x0) + 3000, float(y0) - 2000), marge_blocs=0)
    out = terrain.SourceDEM(e).lignes(g, 0, g.haut)
    assert out.shape == (g.haut, g.larg)
    assert np.allclose(out, 50.0)

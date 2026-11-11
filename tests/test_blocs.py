"""Les blocs : le résultat ne dépend pas du zoom pivot ; une erreur ne laisse rien ; la mer et le relief vide."""
import hashlib
import json
import os

import numpy as np
import pytest

from conftest import emprise_geojson, entrees_relief, osm_xml
from fabrique import fabriquer as fab, manifeste, mvt, osm, terrain
from fabrique.emprise import lire_emprise
from fabrique.verifier import controles, en_tete, lire_tuiles


def empreintes(dossier):
    out = {}
    for r, _, fs in os.walk(dossier):
        for f in fs:
            chemin = os.path.join(r, f)
            rel = os.path.relpath(chemin, dossier)
            if rel == "manifeste.json":
                out[rel] = manifeste.sha256_comparable(json.load(open(chemin, encoding="utf-8")))
            else:
                out[rel] = hashlib.sha256(open(chemin, "rb").read()).hexdigest()
    return out


@pytest.fixture(scope="module")
def traversee(tmp_path_factory):
    """Une emprise de 5 km qui chevauche la limite de deux blocs du zoom 10 (35,859° E), avec une côte à 35,87° E :
    la mer, ses lignes de rivage et les courbes traversent la limite."""
    d = tmp_path_factory.mktemp("traversee")
    jeu = {"dossier": d}
    e = str(d / "e.geojson")
    emprise_geojson(e, (35.83, 35.89, 35.40, 35.46))
    p = str(d / "m.osm")
    osm_xml(p, dlon=-0.14)
    return jeu, e, p


def test_le_zoom_pivot_ne_change_aucun_octet(traversee):
    jeu, e, p = traversee
    em = fab.emprise_merc(lire_emprise(e))
    assert len(fab.blocs_du_pivot(10, em)) == 2 and len(fab.blocs_du_pivot(8, em)) == 1
    res = {}
    for pz in (7, 8, 10):
        s = str(jeu["dossier"] / ("sortie_%d" % pz))
        fab.fabriquer(e, [p], str(jeu["dossier"] / "c"), s, journal=lambda *a: None,
                      entrees_relief=entrees_relief(jeu, "x", cote=35.87), bloc_zoom=pz)
        res[pz] = empreintes(s)
    assert res[8] == res[10] == res[7]
    # la mer, les rivages et les courbes sont bien dans ces tuiles : le test ne compare pas du vide
    couches = set()
    import mapbox_vector_tile
    for z, x, y, data in lire_tuiles(str(jeu["dossier"] / "sortie_10" / "fond.pmtiles")):
        couches |= set(mapbox_vector_tile.decode(data, default_options={"y_coord_down": True}))
    assert {"mer", "rivage", "courbes", "routes", "forets", "grille"} <= couches
    m = json.load(open(jeu["dossier"] / "sortie_10" / "manifeste.json"))
    assert m["execution"]["bloc_zoom"] == 10
    assert "bloc_zoom" not in json.dumps(manifeste.partie_comparable(m))


def test_une_emprise_toute_en_mer_donne_un_relief_vide_et_valide(tmp_path):
    jeu = {"dossier": tmp_path}
    e = str(tmp_path / "e.geojson")
    emprise_geojson(e, (35.50, 35.56, 35.40, 35.46))
    p = str(tmp_path / "m.osm")
    osm_xml(p)
    s = str(tmp_path / "sortie")
    os.makedirs(s)                                    # un dossier vide qui existe déjà est accepté, et remplacé
    fab.fabriquer(e, [p], str(tmp_path / "c"), s, journal=lambda *a: None, entrees_relief=entrees_relief(jeu, "m"))
    h = en_tete(os.path.join(s, "relief.pmtiles"))
    assert h["tile_entries_count"] == 0 and h["tile_data_length"] == 0
    assert list(lire_tuiles(os.path.join(s, "relief.pmtiles"))) == []
    ok, res = controles(s, journal=lambda *a: None)
    assert ok, res["echecs"]
    assert res["tuiles_relief"] == 0 and res["tuiles_fond"] > 0
    assert res["villes"] == 0 and res["bourgs"] == 0


@pytest.mark.parametrize("ou", ["osm.lire", "terrain.construire", "mvt.encoder_tuile", "mvt.ecrire_pmtiles"])
def test_une_erreur_au_milieu_ne_laisse_rien(tmp_path, monkeypatch, ou):
    """Une exception injectée : ni dossier de sortie, ni `.en-cours`, ni dossier de travail (une exception à n'importe
    quel moment)."""
    jeu = {"dossier": tmp_path / "jeu"}
    jeu["dossier"].mkdir()
    e = str(tmp_path / "e.geojson")
    emprise_geojson(e)
    p = str(tmp_path / "m.osm")
    osm_xml(p)
    rel = entrees_relief(jeu, "e")
    parent = tmp_path / "parent"
    parent.mkdir()
    avant = sorted(os.listdir(parent))
    mod, nom = ou.split(".")
    appels = []

    def bombe(*a, **k):
        appels.append(1)
        if len(appels) >= (3 if nom == "encoder_tuile" else 1):
            raise RuntimeError("panne injectée")
        return original(*a, **k)

    module = {"osm": osm, "terrain": terrain, "mvt": mvt}[mod]
    original = getattr(module, nom)
    monkeypatch.setattr(module, nom, bombe)
    with pytest.raises(RuntimeError, match="panne injectée"):
        fab.fabriquer(e, [p], str(tmp_path / "c"), str(parent / "sortie"), journal=lambda *a: None, entrees_relief=rel)
    assert sorted(os.listdir(parent)) == avant == []


def test_bloc_zoom_hors_de_7_a_10_refuse(tmp_path):
    with pytest.raises(ValueError, match="entre 7 et 10"):
        fab.fabriquer("x", [], "c", str(tmp_path / "s"), bloc_zoom=6)
    with pytest.raises(ValueError):
        fab.fabriquer("x", [], "c", str(tmp_path / "s"), bloc_zoom=11)


# --------------------------------------------------------------------------- la mer : une dépression n'est jamais de la mer
def _grille(h=40, w=60):
    dedans = np.ones((h, w), dtype=bool)
    return np.zeros((h, w), dtype=bool), np.zeros((h, w), dtype=bool), dedans


def test_mer_semee_entre_moins_un_demi_et_plus_un_demi_et_reliee_au_bord():
    bas, graine, dedans = _grille()
    # la mer : une bande à l'ouest, semée (altitude 0), reliée au bord
    bas[:, :10] = True
    graine[:, :10] = True
    # une dépression intérieure (mer Morte, Tibériade : -300 m, jamais semée), loin du bord
    bas[15:25, 30:40] = True
    # une dépression qui touche le bord de l'emprise (au nord) sans semence
    bas[0:4, 45:55] = True
    mer = terrain.mer_globale(bas, graine, dedans)
    assert mer[:, :10].all()
    assert not mer[15:25, 30:40].any()                # la dépression intérieure
    assert not mer[0:4, 45:55].any()                  # la dépression du bord, sans semence : pas de la mer
    # la dilatation d'un pixel
    assert mer[20, 10] and not mer[20, 12]


def test_une_depression_reliee_a_la_mer_par_un_couloir_bas_est_inondee():
    bas, graine, dedans = _grille()
    bas[:, :10] = True
    graine[:, :10] = True
    bas[20, 10:30] = True                              # un couloir sous le niveau de la mer
    bas[15:25, 30:40] = True                           # la cuvette au bout
    mer = terrain.mer_globale(bas, graine, dedans)
    assert mer[15:25, 30:40].all()


def test_pas_de_mer_si_aucun_ensemble_ne_touche_le_bord():
    bas, graine, dedans = _grille()
    bas[15:25, 20:30] = True
    graine[15:25, 20:30] = True                        # semé, mais enfermé dans l'emprise
    assert not terrain.mer_globale(bas, graine, dedans).any()

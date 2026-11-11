"""Une fabrication complète sur le jeu minuscule, deux fois : reproductibilité, vérifications, rendu des règles."""
import hashlib
import json
import os
import subprocess
import sys

import mapbox_vector_tile
import numpy as np
import pytest

from conftest import RACINE, entrees_relief, lire_osm
from fabrique import bandes, manifeste
from fabrique.fabriquer import fabriquer
from fabrique.verifier import controles, lire_tuiles


def sha(chemin):
    with open(chemin, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


@pytest.fixture(scope="module")
def deux(jeu):
    """Deux fabrications : la première dans ce processus, la seconde dans un SOUS-PROCESSUS (autre PYTHONHASHSEED, autre
    dossier de sortie, autre cache : le cache est complet, aucun réseau)."""
    a = str(jeu["dossier"] / "sortie_a")
    fabriquer(jeu["emprise"], [jeu["osm"]], str(jeu["dossier"] / "cache_a"), a, journal=lambda *x: None,
              entrees_relief=entrees_relief(jeu, "a"))
    entrees_relief(jeu, "b")
    b = str(jeu["dossier"] / "sortie_b")
    env = dict(os.environ, PYTHONHASHSEED="12345", PYTHONPATH=RACINE)
    r = subprocess.run([sys.executable, "-m", "fabrique", "--debug", "fabriquer", "--emprise", jeu["emprise"], "--osm",
                        jeu["osm"], "--cache-relief", str(jeu["dossier"] / "dalles_b"), "--sortie", b],
                       capture_output=True, text=True, env=env, cwd=RACINE)
    assert r.returncode == 0, r.stderr + r.stdout
    return [a, b]


def fichiers(dossier):
    out = []
    for r, _, fs in os.walk(dossier):
        for f in fs:
            out.append(os.path.relpath(os.path.join(r, f), dossier))
    return sorted(out)


def test_structure_du_dossier(deux):
    a = deux[0]
    for f in ("fond.pmtiles", "relief.pmtiles", "style-clair.json", "style-sombre.json", "LICENCE.txt", "manifeste.json",
              "sprite/papier.json", "sprite/papier.png", "sprite/papier@2x.json", "sprite/papier@2x.png"):
        assert os.path.getsize(os.path.join(a, f)) > 0, f
    for police in ("Special Elite", "JetBrains Mono", "Special Elite,JetBrains Mono"):
        for deb in (0, 256, 512, 768, 1024):
            assert os.path.getsize(os.path.join(a, "glyphs", police, "%d-%d.pbf" % (deb, deb + 255))) > 0
    assert [f for f in os.listdir(os.path.dirname(a)) if "en-cours" in f or "travail" in f] == []


def test_reproductible_a_l_octet_dans_deux_dossiers_et_deux_caches(deux):
    a, b = deux
    assert fichiers(a) == fichiers(b)
    comparables = [f for f in fichiers(a) if f != "manifeste.json"]
    for f in comparables:
        assert sha(os.path.join(a, f)) == sha(os.path.join(b, f)), f
    ma = json.load(open(os.path.join(a, "manifeste.json")))
    mb = json.load(open(os.path.join(b, "manifeste.json")))
    assert manifeste.sha256_comparable(ma) == manifeste.sha256_comparable(mb)
    assert "fabrique_le" in ma


def test_verifier_passe(deux):
    ok, res = controles(deux[0], journal=lambda *a: None)
    assert ok, res["echecs"]
    assert res["villes"] == 1 and res["bourgs"] == 3                      # Bourg, Latakia (name:en), Şağlam (JetBrains Mono)
    assert res["noms"]["town"] == {"ecrits": 3, "tus": 1}                  # le nom géorgien sans name:en se tait


def test_manifeste_complet(deux):
    m = json.load(open(os.path.join(deux[0], "manifeste.json")))
    assert m["entrees"]["osm"][0]["sha256"] and m["entrees"]["osm"][0]["taille"] > 0
    assert all(e["sha256"] for e in m["entrees"]["relief"])
    assert [p["fichier"] for p in m["entrees"]["polices"]] == ["polices/SpecialElite-Regular.ttf", "polices/JetBrainsMono-Regular.ttf"]
    assert m["python"].startswith("3.12") and m["dependances"]["numpy"]
    assert "commit" in m["fabrique"]
    # pas de chemin absolu ni de date hors de fabrique_le
    reste = json.dumps(manifeste.partie_comparable(m))
    assert "/tmp" not in reste and "/home" not in reste and "fabrique_le" not in reste


def tuiles_decodees(dossier):
    return {(z, x, y): mapbox_vector_tile.decode(d, default_options={"y_coord_down": True})
            for z, x, y, d in lire_tuiles(os.path.join(dossier, "fond.pmtiles"))}


def test_ni_village_ni_hameau_dans_les_tuiles(deux):
    for k, t in tuiles_decodees(deux[0]).items():
        for nom, c in t.items():
            for f in c["features"]:
                if "place" in f["properties"]:
                    assert nom == "lieux" and f["properties"]["place"] in ("city", "town")
                assert f["properties"].get("nom") not in ("Village", "Hameau", "Ferme", "Lieu-dit")


def test_couches_et_classes_de_routes(deux):
    couches, classes = set(), set()
    for t in tuiles_decodees(deux[0]).values():
        for nom, c in t.items():
            couches.add(nom)
            if nom == "routes":
                classes |= {f["properties"]["classe"] for f in c["features"]}
    assert {"mer", "rivage", "eau", "cours_eau", "courbes", "forets", "localites", "lieux", "routes", "rail",
            "pistes", "frontieres", "grille"} <= couches
    assert classes == {"motorway", "primary", "secondary"}           # ni tertiary, ni autre


def test_liens_seulement_aux_bandes_11_et_plus(deux):
    d = tuiles_decodees(deux[0])
    def nb_motorway(z):
        n = 0
        for (zz, x, y), t in d.items():
            if zz == z and "routes" in t:
                n += sum(1 for f in t["routes"]["features"] if f["properties"]["classe"] == "motorway")
        return n
    assert nb_motorway(14) > nb_motorway(10) >= 1


def test_forets_en_points_jamais_en_aplat(deux):
    types = set()
    for t in tuiles_decodees(deux[0]).values():
        for f in t.get("forets", {}).get("features", []):
            types.add(f["geometry"]["type"])
    assert types == {"Point"}


def _points_de_la_couche(tuiles, couche, zoom, filtre=lambda f: True):
    from fabrique import merc
    pts = []
    for (z, x, y), t in tuiles.items():
        if z != zoom:
            continue
        xmin, ymin, xmax, ymax = merc.bornes_tuile(z, x, y)
        k = (xmax - xmin) / 4096
        for f in t.get(couche, {}).get("features", []):
            if not filtre(f):
                continue
            g = f["geometry"]
            lignes = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]
            for l in lignes:
                pts += [(xmin + cx * k, ymax - cy * k) for cx, cy in l]
    return pts


@pytest.mark.parametrize("b", bandes.BANDES)
def test_les_pistes_ne_s_ecartent_pas_de_la_source_a_chaque_bande(jeu, deux, tmp_path, b):
    """Les pistes ne tremblent jamais : l'écart à la source n'est que l'arrondi à la grille MVT (une unité = tuile / 4096),
    soit au plus 1 m aux zooms 13 et 14 ; plus haut dans les bandes, la demi-diagonale d'une unité."""
    from fabrique import merc
    from shapely.geometry import Point
    d, _ = lire_osm(jeu["osm"], tmp_path / "t")
    piste = d["pistes"][0][2]
    tuiles = tuiles_decodees(deux[0])
    for z in range(b[0], b[1] + 1):
        pts = _points_de_la_couche(tuiles, "pistes", z)
        assert pts, (b, z)
        unite = merc.pixel(z) * 512 / 4096
        ecart = max(piste.distance(Point(p)) for p in pts)
        assert ecart <= max(1.0, 0.72 * unite), (b, z, ecart, unite)


def test_la_grille_reste_droite(deux):
    """La grille n'est pas tremblée : chaque ligne de 10 km d'une tuile est rectiligne à la tolérance de l'arrondi."""
    for (z, x, y), t in tuiles_decodees(deux[0]).items():
        if z != 14:
            continue
        for f in t.get("grille", {}).get("features", []):
            if f["properties"].get("type") != "ligne":
                continue
            g = f["geometry"]
            lignes = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]
            for l in lignes:
                a = np.array(l, dtype=float)
                if len(a) < 3:
                    continue
                d0, d1 = a[0], a[-1]
                n = np.hypot(*(d1 - d0))
                if n < 50:
                    continue
                v = d1 - d0
                dist = np.abs(v[0] * (a[:, 1] - d0[1]) - v[1] * (a[:, 0] - d0[0])) / n
                assert dist.max() < 3.0                  # en unités MVT (< 2 m)


@pytest.mark.parametrize("b", bandes.BANDES)
def test_le_trait_tremble_entre_un_demi_et_un_et_demi_pixel_au_zoom_median_de_chaque_bande(b):
    """La fonction de la fabrique (simplification comprise) sur une droite de 400 pixels du zoom médian de la bande."""
    from shapely.geometry import LineString
    from fabrique import couches
    L = 400 * bandes.px(b)
    droite = LineString([(1.0e6, 4.0e6), (1.0e6 + L / 2, 4.0e6), (1.0e6 + L, 4.0e6)])
    for ident in ("c1", "c2", "w3"):
        t = couches.tremble_ligne(droite, b, ident)
        crete = max(abs(y - 4.0e6) for _, y in t.coords) / bandes.px(b)
        assert 0.5 <= crete <= 1.5, (b, ident, crete)


def test_les_routes_tremblent_a_chaque_bande(jeu, deux, tmp_path):
    """Une route n'est jamais exactement sa source : aux bandes où elle est assez longue, ses sommets s'écartent d'au moins 0,3 pixel."""
    from fabrique import merc
    from shapely.geometry import Point
    d, _ = lire_osm(jeu["osm"], tmp_path / "t")
    primaire = [g for _, pr, g in d["routes"] if pr["classe"] == "primary"][0]
    tuiles = tuiles_decodees(deux[0])
    for b in bandes.BANDES[3:]:          # aux bandes basses, une route de 6 km ne fait que quelques pixels : ses extrémités, fixes, absorbent le trait
        z = b[0] + 1
        ecarts = [primaire.distance(Point(p)) / merc.pixel(z)
                  for p in _points_de_la_couche(tuiles, "routes", z, lambda f: f["properties"]["classe"] == "primary")]
        assert ecarts and 0.3 < max(ecarts) < 3.0, (b, max(ecarts) if ecarts else None)


def test_emprise_aucune_tuile_en_dehors(jeu, deux):
    ok, res = controles(deux[0], journal=lambda *a: None)
    assert ok
    assert res["tuiles_fond"] > 0 and res["tuiles_relief"] > 0


def test_vector_layers_liste_toutes_les_couches_presentes_avec_leurs_champs(deux):
    from fabrique.verifier import en_tete
    from pmtiles.reader import MmapSource, Reader
    with open(os.path.join(deux[0], "fond.pmtiles"), "rb") as f:
        meta = Reader(MmapSource(f)).metadata()
    declarees = {v["id"]: v["fields"] for v in meta["vector_layers"]}
    presentes = {nom for t in tuiles_decodees(deux[0]).values() for nom in t}
    assert presentes == set(declarees)
    assert all(isinstance(champs, dict) for champs in declarees.values())
    assert "classe" in declarees["routes"]
    assert en_tete(os.path.join(deux[0], "fond.pmtiles"))["min_zoom"] == bandes.ZOOM_MIN_FOND


def test_verifier_rend_1_sur_toute_erreur_avec_le_message(tmp_path, capsys, monkeypatch):
    from fabrique import cli, verifier as v
    assert cli.principal(["verifier", str(tmp_path / "n_existe_pas")]) == 1
    assert "ÉCHEC" in capsys.readouterr().out

    def casse(dossier):
        raise RuntimeError("tuile illisible")
    monkeypatch.setattr(v, "verifier", casse)
    assert cli.principal(["verifier", str(tmp_path)]) == 1
    assert "erreur : tuile illisible" in capsys.readouterr().err
    (tmp_path / "vide").mkdir()
    assert cli.principal(["verifier", str(tmp_path / "vide")]) == 1

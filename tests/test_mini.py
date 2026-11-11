"""Une fabrication complète sur le jeu minuscule, deux fois : reproductibilité, vérifications, rendu des règles."""
import hashlib
import json
import os

import mapbox_vector_tile
import numpy as np
import pytest

from conftest import entrees_relief
from fabrique import bandes, manifeste, osm
from fabrique.fabriquer import fabriquer
from fabrique.verifier import controles, lire_tuiles


def sha(chemin):
    with open(chemin, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


@pytest.fixture(scope="module")
def deux(jeu):
    sorties = []
    for nom in ("a", "b"):
        s = str(jeu["dossier"] / ("sortie_" + nom))
        fabriquer(jeu["emprise"], [jeu["osm"]], str(jeu["dossier"] / ("cache_" + nom)), s,
                  journal=lambda *a: None, entrees_relief=entrees_relief(jeu, nom))
        sorties.append(s)
    return sorties


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
    for police in ("Special Elite", "JetBrains Mono"):
        for deb in (0, 256, 512, 768, 1024):
            assert os.path.getsize(os.path.join(a, "glyphs", police, "%d-%d.pbf" % (deb, deb + 255))) > 0
    assert not os.path.exists(a + ".en-cours")


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
    assert res["villes"] == 1 and res["bourgs"] == 1


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
    tuiles, _ = lire_tuiles(os.path.join(dossier, "fond.pmtiles"))
    return {(z, x, y): mapbox_vector_tile.decode(d, default_options={"y_coord_down": True}) for z, x, y, d in tuiles}


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


def test_pistes_et_grille_restent_droites(jeu, deux):
    """Le trait tremblé ne touche ni les pistes ni la grille : leurs sommets sont ceux de la source."""
    d = osm.lire([jeu["osm"]])
    piste = d.pistes[0][1]
    tuiles = tuiles_decodees(deux[0])
    pts = []
    for (z, x, y), t in tuiles.items():
        if z != 14:
            continue
        from fabrique import merc
        xmin, ymin, xmax, ymax = merc.bornes_tuile(z, x, y)
        k = (xmax - xmin) / 4096
        for f in t.get("pistes", {}).get("features", []):
            g = f["geometry"]
            lignes = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]
            for l in lignes:
                pts += [(xmin + cx * k, ymax - cy * k) for cx, cy in l]
    assert pts
    ecart = max(piste.distance(__import__("shapely").geometry.Point(p)) for p in pts)
    assert ecart < 6.0                                  # moins que l'arrondi MVT (2,4 m) fois deux
    # la grille : chaque ligne de 10 km d'une tuile est rectiligne à la tolérance de l'arrondi
    for (z, x, y), t in tuiles.items():
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
                assert dist.max() < 3.0                  # en unités MVT (< 7 m)


def test_les_routes_tremblent_a_chaque_bande(jeu, deux):
    """Une route n'est jamais exactement sa source : aux bandes où elle est assez longue, ses sommets s'écartent d'au moins 0,3 pixel."""
    from fabrique import merc
    from shapely.geometry import Point
    d = osm.lire([jeu["osm"]])
    primaire = [l for _, c, ln, l in d.routes if c == "primary"][0]
    tuiles = tuiles_decodees(deux[0])
    for b in bandes.BANDES[3:]:          # aux bandes basses, une route de 6 km ne fait que quelques pixels : ses extrémités, fixes, absorbent le trait
        z = b[0] + 1
        ecarts = []
        for (zz, x, y), t in tuiles.items():
            if zz != z:
                continue
            xmin, ymin, xmax, ymax = merc.bornes_tuile(zz, x, y)
            k = (xmax - xmin) / 4096
            for f in t.get("routes", {}).get("features", []):
                if f["properties"]["classe"] != "primary":
                    continue
                g = f["geometry"]
                for l in (g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]):
                    ecarts += [primaire.distance(Point(xmin + cx * k, ymax - cy * k)) / merc.pixel(zz) for cx, cy in l]
        assert ecarts and 0.3 < max(ecarts) < 3.0, (b, max(ecarts) if ecarts else None)


def test_emprise_aucune_tuile_en_dehors(jeu, deux):
    ok, res = controles(deux[0], journal=lambda *a: None)
    assert ok
    assert res["tuiles_fond"] > 0 and res["tuiles_relief"] > 0

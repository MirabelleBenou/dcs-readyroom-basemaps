import numpy as np

from conftest import lire_osm, osm_xml
from fabrique import glyphes, merc, osm

import os
RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def lieux(d):
    return sorted((p["place"], p["nom"]) for _, p, _ in d["lieux"])


def test_aucun_village_ni_hameau_ni_lieu_dit(tmp_path):
    p = str(tmp_path / "m.osm")
    osm_xml(p)
    d, _ = lire_osm(p, tmp_path / "t")
    l = lieux(d)
    assert ("city", "Ville") in l and ("town", "Bourg") in l
    assert not any(c in ("village", "hamlet", "isolated_dwelling", "locality") for c, _ in l)
    assert "Village" not in [n for _, n in l] and "Hameau" not in [n for _, n in l]


def test_nom_name_en_d_abord_puis_int_name_puis_name():
    assert osm.nom_en_usage({"name": "A", "name:en": "B"}) == "B"
    assert osm.nom_en_usage({"name": "A", "int_name": "C"}) == "C"
    assert osm.nom_en_usage({"name": "A", "int_name": "C", "name:en": "B"}) == "B"
    assert osm.nom_en_usage({"name": "A"}) == "A"
    assert osm.nom_en_usage({"name:en": " "}) is None
    assert osm.nom_en_usage({}) is None


def test_le_nom_arabe_cede_a_name_en(tmp_path):
    p = str(tmp_path / "m.osm")
    osm_xml(p)
    d, _ = lire_osm(p, tmp_path / "t")
    noms = [n for _, n in lieux(d)]
    assert "Latakia" in noms and not any("ا" in n for n in noms)


def test_les_noms_ecrits_et_tus_avec_la_pile_de_polices(tmp_path):
    """Un nom turc en ş et ğ s'écrit (JetBrains Mono les a) ; un nom géorgien sans name:en se tait, avec son identifiant."""
    p = str(tmp_path / "m.osm")
    osm_xml(p)
    polices = glyphes.charger_polices(os.path.join(RACINE, "polices"))
    assert not any(ord(c) in polices["Special Elite"].cmap for c in "şğ")       # Special Elite ne les a pas
    assert all(ord(c) in polices["JetBrains Mono"].cmap for c in "şğ")
    d, lecture = lire_osm(p, tmp_path / "t", couvre=lambda t: glyphes.pile_couvre(polices, t))
    noms = [n for _, n in lieux(d)]
    assert "Şağlam" in noms
    assert not any("თ" in n for n in noms)
    r = lecture.resume()
    assert len(r["town"]["tus"]) == 1 and r["town"]["tus"][0].startswith("n")
    assert [n for _, n in r["town"]["ecrits"]].count("Şağlam") == 1
    assert r["city"]["tus"] == []


def test_classes_de_routes_et_bretelles(tmp_path):
    p = str(tmp_path / "m.osm")
    osm_xml(p)
    d, _ = lire_osm(p, tmp_path / "t")
    assert sorted((pr["classe"], pr["lien"]) for _, pr, _ in d["routes"]) == [
        ("motorway", 0), ("motorway", 1), ("primary", 0), ("secondary", 0)]
    assert len(d["pistes"]) == 1 and len(d["rail"]) == 1 and len(d["eau"]) == 1 and len(d["forets"]) == 1
    assert sorted(pr["classe"] for _, pr, _ in d["cours_eau"]) == ["river", "stream"]


def test_frontieres_admin_level_2_seulement_sans_limites_maritimes(tmp_path):
    p = str(tmp_path / "m.osm")
    osm_xml(p)
    d, _ = lire_osm(p, tmp_path / "t")
    assert [i for i, _, _ in d["frontieres"]] == ["c13"]


def test_l_osm_hors_de_la_boite_de_l_emprise_n_entre_pas_au_magasin(tmp_path):
    from shapely.geometry import box
    p = str(tmp_path / "m.osm")
    osm_xml(p)
    x0, y0 = merc.lonlat_vers_merc(36.5, 35.0)
    x1, y1 = merc.lonlat_vers_merc(36.6, 35.1)
    d, _ = lire_osm(p, tmp_path / "t", emprise_merc=box(float(x0), float(y0), float(x1), float(y1)))
    assert all(not v for v in d.values())


def test_chaines_recollees_par_les_noeuds_pas_par_les_coordonnees():
    # trois chemins bout à bout (1-2, 2-3, 3-4) + un quatrième qui rejoint 3 : le nœud 3 est de degré 3, rien ne s'y recolle
    a = np.array([1, 2, 3, 3])
    b = np.array([2, 3, 4, 9])
    ch = osm.chaines(a, b)
    assert [[i for i, _ in c] for c in ch] == [[0, 1], [2], [3]]
    # un chemin parcouru à l'envers
    a = np.array([1, 3])
    b = np.array([2, 2])
    ch = osm.chaines(a, b)
    assert ch == [[(0, 1), (1, -1)]]
    # un chemin fermé se suffit ; un anneau de deux chemins tourne
    assert osm.chaines(np.array([5]), np.array([5])) == [[(0, 1)]]
    anneau = osm.chaines(np.array([1, 2]), np.array([2, 1]))
    assert len(anneau) == 1 and len(anneau[0]) == 2


def test_ruisseau_filtre_apres_le_recollage(tmp_path):
    """Des tronçons de 1,2 km qui forment un ruisseau de 3,6 km : gardé (le filtre porte sur la ligne recollée)."""
    pts = [(36.00 + 0.0108 * i, 35.40) for i in range(4)]            # 3 tronçons d'environ 1 km
    nodes = ['<node id="%d" lat="%.6f" lon="%.6f"/>' % (i + 1, la, lo) for i, (lo, la) in enumerate(pts)]
    ways = ['<way id="%d"><nd ref="%d"/><nd ref="%d"/><tag k="waterway" v="stream"/></way>' % (10 + i, i + 1, i + 2)
            for i in range(3)]
    p = tmp_path / "s.osm"
    p.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<osm version="0.6">\n' + "\n".join(nodes + ways) + "\n</osm>\n")
    d, _ = lire_osm(str(p), tmp_path / "t")
    assert len(d["cours_eau"]) == 1
    assert 2500 < osm.longueur_sol(np.asarray(d["cours_eau"][0][2].coords)) < 4000
    # un seul tronçon de 1,2 km n'atteint pas 2,5 km : écarté
    p2 = tmp_path / "s2.osm"
    p2.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<osm version="0.6">\n' + "\n".join(nodes[:2] + ways[:1]) + "\n</osm>\n")
    d2, _ = lire_osm(str(p2), tmp_path / "t2")
    assert d2["cours_eau"] == []

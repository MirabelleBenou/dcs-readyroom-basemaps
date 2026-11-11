from fabrique import grille, merc
from fabrique.emprise import lire_emprise
from fabrique.fabriquer import emprise_merc
from conftest import emprise_geojson


def _emprise(tmp_path, nom="e.geojson"):
    """Une vingtaine de kilomètres de côté, à cheval sur 36° E : assez pour des angles de grille de 10 km."""
    p = str(tmp_path / nom)
    emprise_geojson(p, (35.9, 36.1, 35.35, 35.5))
    wgs = lire_emprise(p)
    return emprise_merc(wgs), wgs


def test_la_limite_des_fuseaux_36_et_37_coupe_les_lignes(tmp_path):
    em, wgs = _emprise(tmp_path)
    x36 = float(merc.lonlat_vers_merc(36.0, 0)[0])
    parties = grille.parties_par_fuseau(em, wgs)
    assert [z for z, _ in parties] == [36, 37]
    lignes, points = grille.construire_grille(em, wgs, 10000, carres=True)
    # une ligne de 10 km de chaque fuseau, jamais de l'autre côté de 36° E
    par_fuseau = {36: [], 37: []}
    for z, part in parties:
        for g, _ in lignes:
            if part.buffer(1.0).contains(g):
                par_fuseau[z].append(g)
    assert par_fuseau[36] and par_fuseau[37]
    for g in par_fuseau[36]:
        assert g.bounds[2] <= x36 + 1.0
    for g in par_fuseau[37]:
        assert g.bounds[0] >= x36 - 1.0
    assert all(len(g.coords) >= 2 for g, _ in lignes)


def test_etiquettes_a_deux_chiffres_et_carres_de_100_km(tmp_path):
    em, wgs = _emprise(tmp_path)
    _, points = grille.construire_grille(em, wgs, 10000, carres=True)
    textes = [p["texte"] for _, p in points if p["type"] in ("e", "n")]
    assert textes and all(len(t) == 2 and t.isdigit() for t in textes)
    assert sorted(p["texte"] for _, p in points if p["type"] == "carre") == ["36S YE", "37S BV"]
    # aux bandes 5-6 et 7-8 (lignes de 100 km), pas de carré de 100 km
    _, pts = grille.construire_grille(em, wgs, 100000, carres=False)
    assert not any(p["type"] == "carre" for _, p in pts)


def test_lettres_mgrs_comme_planche():
    assert grille.carre_100km(36, 770000, 3927000) == "YE"
    assert grille.carre_100km(37, 230000, 3927000) == "BV"
    assert grille.lettre_bande(35.4) == "S"


def test_la_grille_ne_depasse_pas_l_emprise(tmp_path):
    em, wgs = _emprise(tmp_path)
    lignes, points = grille.construire_grille(em, wgs, 10000, carres=True)
    tamp = em.buffer(1.0)
    assert all(tamp.contains(g) for g, _ in lignes)
    assert all(tamp.contains(g) for g, _ in points)

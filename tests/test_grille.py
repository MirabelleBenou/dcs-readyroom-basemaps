import numpy as np

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


def test_lettres_mgrs_comme_l_esquisse():
    assert grille.carre_100km(36, 770000, 3927000) == "YE"
    assert grille.carre_100km(37, 230000, 3927000) == "BV"
    assert grille.lettre_bande(35.4) == "S"


def test_la_grille_ne_depasse_pas_l_emprise(tmp_path):
    em, wgs = _emprise(tmp_path)
    lignes, points = grille.construire_grille(em, wgs, 10000, carres=True)
    tamp = em.buffer(1.0)
    assert all(tamp.contains(g) for g, _ in lignes)
    assert all(tamp.contains(g) for g, _ in points)


def test_chaque_ligne_est_dans_exactement_un_fuseau_et_aucune_ne_traverse_36_E(tmp_path):
    em, wgs = _emprise(tmp_path)
    parties = grille.parties_par_fuseau(em, wgs)
    for pas in (10000, 100000):
        lignes, _ = grille.construire_grille(em, wgs, pas, carres=False)
        assert lignes or pas == 100000         # une emprise de 20 km n'a pas forcément de ligne de 100 km
        for g, pr in lignes:
            dans = [z for z, part in parties if part.buffer(1.0).contains(g)]
            assert dans == [pr["zone"]], (pas, pr, dans)               # exactement un fuseau, celui de la ligne
            lon = merc.merc_vers_lonlat(*np.asarray(g.coords).T)[0]
            if pr["zone"] == 36:
                assert lon.max() <= 36.0 + 1e-6
            else:
                assert lon.min() >= 36.0 - 1e-6


def test_un_carre_a_cheval_sur_32_N_a_deux_etiquettes(tmp_path):
    """La lettre de bande change à 32° N (R au sud, S au nord) : le carré de 100 km qui chevauche cette latitude a une
    étiquette par morceau de bande."""
    p = str(tmp_path / "e.geojson")
    emprise_geojson(p, (35.0, 35.4, 31.8, 32.2))
    wgs = lire_emprise(p)
    em = emprise_merc(wgs)
    _, points = grille.construire_grille(em, wgs, 10000, carres=True)
    carres = sorted(pr["texte"] for _, pr in points if pr["type"] == "carre")
    bandes_ = {t.split()[0] for t in carres}
    assert bandes_ == {"36R", "36S"}
    par_carre = {}
    for t in carres:
        zb, c = t.split()
        par_carre.setdefault(c, set()).add(zb)
    assert any(len(v) == 2 for v in par_carre.values())              # un même carré, deux étiquettes
    # une emprise loin de toute limite n'en a qu'une
    em2, wgs2 = _emprise(tmp_path)
    _, pts2 = grille.construire_grille(em2, wgs2, 10000, carres=True)
    assert sorted(pr["texte"] for _, pr in pts2 if pr["type"] == "carre") == ["36S YE", "37S BV"]


def test_hemisphere_sud_refuse_avec_un_message(tmp_path):
    p = str(tmp_path / "s.geojson")
    emprise_geojson(p, (35.0, 35.4, -1.0, 1.0))
    wgs = lire_emprise(p)
    import pytest
    with pytest.raises(grille.HemisphereErreur, match="hémisphère nord"):
        grille.construire_grille(emprise_merc(wgs), wgs, 10000)
    with pytest.raises(grille.HemisphereErreur):
        grille.verifier_hemisphere(wgs)

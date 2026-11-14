"""La mer par bandes égale la `mer_globale` de la base, pixel pour pixel, à toutes les hauteurs de bande."""
import os
import sys

import numpy as np
import pytest

from conftest import RACINE
from mer_reference import mer_globale_base
from fabrique import terrain
from fabrique.terrain import MerParBandes, mer_globale

sys.path.insert(0, os.path.join(RACINE, "essais"))
from mer_synthetique import MerSynthetique  # noqa: E402


def par_bandes(bas, graine, dedans, hauteur):
    haut, larg = bas.shape
    m = MerParBandes(haut, larg, lambda a, b: dedans[a:b])
    for i0 in range(0, haut, hauteur):
        i1 = min(haut, i0 + hauteur)
        m.ajouter(i0, bas[i0:i1], graine[i0:i1])
    m.resoudre()
    return m.bande(0, haut), m


def egale(bas, graine, dedans, hauteurs):
    attendu = mer_globale_base(bas, graine, dedans)
    for h in hauteurs:
        obtenu, m = par_bandes(bas, graine, dedans, h)
        assert (obtenu == attendu).all(), "hauteur de bande %d : %d pixels de différence" % (h, (obtenu != attendu).sum())
        assert m.compte() == int(attendu.sum())
    return attendu


def tout_dedans_sauf_marge(forme, m=1):
    d = np.zeros(forme, dtype=bool)
    d[m:forme[0] - m, m:forme[1] - m] = True
    return d


def vide(h, l):
    return np.zeros((h, l), dtype=bool)


@pytest.mark.parametrize("graine_alea", range(25))
def test_grilles_aleatoires(graine_alea):
    rng = np.random.default_rng(graine_alea)
    h, l = int(rng.integers(1, 45)), int(rng.integers(1, 60))
    bas = rng.random((h, l)) < rng.choice([0.2, 0.45, 0.6, 0.75])
    graine = bas & (rng.random((h, l)) < rng.choice([0.01, 0.05, 0.3]))
    dedans = rng.random((h, l)) < 0.9 if graine_alea % 3 == 0 else tout_dedans_sauf_marge((h, l), int(rng.integers(0, 3)))
    egale(bas, graine, dedans, sorted({1, 2, 3, 7, 16, h}))


def test_u_qui_traverse_les_limites_de_bandes():
    h, l = 30, 30
    bas = vide(h, l)
    bas[2:28, 3] = True            # la branche gauche, sur toute la hauteur
    bas[2:28, 12] = True           # la branche droite
    bas[27, 3:13] = True           # le fond du U
    graine = vide(h, l)
    graine[27, 8] = True
    dedans = tout_dedans_sauf_marge((h, l), 2)
    dedans[:, 12:] = False         # le bord de l'emprise ne touche que la branche droite, par le haut
    mer = egale(bas, graine, dedans, range(1, h + 1))
    assert mer.any()


def test_spirale():
    h = l = 23
    bas = vide(h, l)
    # une spirale carrée d'un pixel d'épaisseur, un pixel de terre entre ses tours : un seul ensemble, du centre au bord
    y = x = 11
    bas[y, x] = True
    dirs = ((0, 1), (1, 0), (0, -1), (-1, 0))
    d, n, fini = 0, 2, False
    while not fini:
        for _ in range(2):
            dy, dx = dirs[d % 4]
            for _ in range(n):
                y, x = y + dy, x + dx
                if not (1 <= y < h - 1 and 1 <= x < l - 1):
                    fini = True
                    break
                bas[y, x] = True
            if fini:
                break
            d += 1
        n += 2
    graine = vide(h, l)
    graine[11, 11] = True          # la graine est au centre, le bord à l'autre bout de la spirale
    dedans = tout_dedans_sauf_marge((h, l), 1)
    mer = egale(bas, graine, dedans, range(1, h + 1))
    assert mer[11, 11] and mer[1, 5]


def test_couloir_d_un_pixel_relie_et_la_diagonale_ne_relie_pas():
    h, l = 24, 24
    dedans = tout_dedans_sauf_marge((h, l), 1)
    # un lac plein et un couloir d'un pixel jusqu'au bord de l'emprise : relié
    bas = vide(h, l)
    bas[8:14, 8:14] = True
    bas[11, 1:8] = True
    graine = vide(h, l)
    graine[10, 10] = True
    assert egale(bas, graine, dedans, range(1, h + 1))[10, 10]
    # le même, mais le couloir ne touche le lac que par un coin : rien ne relie en connexité 4
    bas = vide(h, l)
    bas[8:14, 8:14] = True
    for k in range(7):
        bas[7 - k, 7 - k] = True    # une diagonale qui part du coin (8, 8) vers le bord
    bas[8:14, 8:14] = True
    graine = vide(h, l)
    graine[10, 10] = True
    mer = egale(bas, graine, dedans, range(1, h + 1))
    assert not mer[10, 10]
    # un contact en diagonale seule entre un ensemble du bord sans graine et un lac avec graine
    bas = vide(h, l)
    bas[1:4, 1:4] = True            # touche le bord de l'emprise (ligne 1 et colonne 1), sans graine
    bas[4:8, 4:8] = True            # son coin (4, 4) touche (3, 3) en diagonale seulement
    graine = vide(h, l)
    graine[5, 5] = True
    mer = egale(bas, graine, dedans, range(1, h + 1))
    assert not mer.any()


def test_tout_en_mer_et_rien_en_mer():
    dedans = tout_dedans_sauf_marge((12, 12), 1)
    pleine = np.ones((12, 12), dtype=bool)
    egale(pleine, pleine, dedans, (1, 5, 12))
    assert not egale(vide(12, 12), vide(12, 12), dedans, (1, 5)).any()
    assert not egale(pleine, vide(12, 12), dedans, (1, 5)).any()                  # pas de graine


def test_bandes_dans_l_ordre_et_sans_trou():
    m = MerParBandes(10, 10, lambda a, b: np.ones((b - a, 10), dtype=bool))
    with pytest.raises(ValueError):
        m.ajouter(3, vide(2, 10), vide(2, 10))
    m.ajouter(0, vide(4, 10), vide(4, 10))
    with pytest.raises(ValueError):
        m.resoudre()


def test_mer_globale_remplace_la_base():
    rng = np.random.default_rng(3)
    bas = rng.random((50, 70)) < 0.6
    graine = bas & (rng.random((50, 70)) < 0.02)
    dedans = tout_dedans_sauf_marge((50, 70), 2)
    assert (mer_globale(bas, graine, dedans) == mer_globale_base(bas, graine, dedans)).all()
    assert (mer_globale(bas, graine, dedans, hauteur_bande=1) == mer_globale_base(bas, graine, dedans)).all()


@pytest.mark.parametrize("graine_gen", (7, 11))
def test_generateur_synthetique_reduit_egale_la_base(graine_gen):
    g = MerSynthetique(1750, 2000, graine=graine_gen)
    bas, graine, dedans = g.tout()
    attendu = mer_globale_base(bas, graine, dedans)
    assert 0 < attendu.sum() < attendu.size
    for h in (64, 1750 // 7, 1):
        m = MerParBandes(g.haut, g.larg, g.dedans)
        for i0 in range(0, g.haut, h):
            i1 = min(g.haut, i0 + h)
            b, gr = g.bande(i0, i1)
            m.ajouter(i0, b, gr)
        m.resoudre()
        assert (m.bande(0, g.haut) == attendu).all(), h
        assert m.compte() == int(attendu.sum())
    # ce que le générateur promet : la grande dépression est de la mer (couloir), l'étang du bord et le lac en diagonale non
    ci, cj, _, _ = g.grande
    assert attendu[ci, cj]
    ci, cj, _, _ = g.petite
    assert attendu[ci, cj]
    r0, r1, c0, c1 = g.lac_diagonal
    assert not attendu[(r0 + r1) // 2, (c0 + c1) // 2]
    r0, r1, c0, c1 = g.etang
    assert not attendu[(r0 + r1) // 2, (c0 + c1) // 2]


def test_dedans_par_bande_egale_la_rasterisation_entiere():
    """La fenêtre d'une bande donne les mêmes pixels que la rastérisation de toute la grille."""
    from rasterio.features import rasterize
    from rasterio.transform import from_origin
    from shapely.geometry import Polygon
    from fabrique import merc
    em = Polygon([(35.0, 35.0), (36.2, 35.1), (36.1, 36.0), (35.4, 36.3)])
    x, y = merc.lonlat_vers_merc(np.array(em.exterior.coords)[:, 0], np.array(em.exterior.coords)[:, 1])
    from shapely.geometry import Polygon as P
    em_m = P(np.column_stack([x, y]))
    g = terrain.Grille(em_m.bounds)
    f10 = 1 << (terrain.Z_GRILLE - terrain.Z_MER)
    h10, w10 = g.haut // f10, g.larg // f10
    px10 = merc.pixel(terrain.Z_MER)
    ox, oy, _, _ = g.niveau(terrain.Z_MER)
    tr = from_origin(-merc.DEMI_MONDE + ox * px10, merc.DEMI_MONDE - oy * px10, px10, px10)
    plein = rasterize([(em_m, 1)], out_shape=(h10, w10), transform=tr, fill=0, all_touched=True, dtype="uint8").astype(bool)
    f = terrain.dedans_par_bande(g, em_m)
    for pas in (1, 7, 64, h10):
        for i0 in range(0, h10, pas):
            i1 = min(h10, i0 + pas)
            assert (f(i0, i1) == plein[i0:i1]).all()

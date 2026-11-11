import numpy as np
import pytest
from shapely.geometry import LineString

from fabrique import bandes, bruit


@pytest.mark.parametrize("b", bandes.BANDES)
def test_amplitude_entre_un_demi_et_un_et_demi_pixel_au_zoom_median(b):
    """Une ligne droite de 400 px de long : le déplacement crête, en pixels du zoom médian de la bande."""
    L = 400 * bandes.px(b)
    droite = np.column_stack([np.linspace(0, L, 5), np.zeros(5)])
    for graine in (1, 2, 3):
        out = bruit.tremble(droite, graine, bandes.amplitude(b), bandes.longueur_onde(b))
        crete = np.abs(out[:, 1]).max() / bandes.px(b)
        assert 0.5 <= crete <= 1.5, (b, graine, crete)


@pytest.mark.parametrize("b", bandes.BANDES)
def test_longueur_d_onde_environ_70_pixels(b):
    L = 700 * bandes.px(b)
    droite = np.column_stack([np.linspace(0, L, 3), np.zeros(3)])
    out = bruit.tremble(droite, 7, bandes.amplitude(b), bandes.longueur_onde(b))
    assert bandes.longueur_onde(b) == pytest.approx(70 * bandes.px(b))
    # le signal est lisse : pas de saut entre sommets voisins de plus de 0,5 px
    assert np.abs(np.diff(out[:, 1])).max() / bandes.px(b) < 1.0


def test_extremites_fixes_et_meme_graine_meme_trait():
    c = np.array([[0.0, 0.0], [5000.0, 800.0], [9000.0, 0.0]])
    a = bruit.tremble(c, 42, 50.0, 700.0)
    b = bruit.tremble(c, 42, 50.0, 700.0)
    assert np.array_equal(a, b)
    assert np.allclose(a[0], c[0]) and np.allclose(a[-1], c[-1])
    assert not np.array_equal(a, bruit.tremble(c, 43, 50.0, 700.0))


def test_un_anneau_se_ferme_apres_deplacement():
    t = np.linspace(0, 2 * np.pi, 41)
    anneau = np.column_stack([3000 * np.cos(t), 3000 * np.sin(t)])
    out = bruit.tremble(anneau, 5, 30.0, 400.0, ferme=True)
    assert np.allclose(out[0], out[-1])
    assert LineString(out).is_ring
    # la jonction est lisse : l'écart entre le dernier et le premier segment reste d'une longueur de segment
    pas = np.hypot(*np.diff(out, axis=0).T)
    assert pas.max() < 3 * pas.mean()


def test_graine_stable_et_independante_de_l_ordre():
    assert bruit.graine("w123", 5, 9) == bruit.graine("w123", 5, 9)
    assert bruit.graine("w123", 5, 9) != bruit.graine("w124", 5, 9)
    u1, v1 = bruit.hasard_cellules(np.array([1, 2, 3]), np.array([4, 5, 6]), 9)
    u2, v2 = bruit.hasard_cellules(np.array([3, 1, 2]), np.array([6, 4, 5]), 9)
    assert np.allclose(sorted(u1), sorted(u2)) and u1[0] == u2[1]
    assert ((u1 >= 0) & (u1 < 1)).all() and ((v1 >= 0) & (v1 < 1)).all()


def test_ferme_veut_dire_extremites_egales_pas_proches():
    """Aux coordonnées Mercator (4e6 m), `np.allclose` par défaut tolère une quarantaine de mètres : un anneau ouvert de 42 m
    n'est pas fermé."""
    ouvert = np.array([[4.0e6, 4.2e6], [4.0e6 + 3000, 4.2e6], [4.0e6 + 3000, 4.2e6 + 3000], [4.0e6 + 30.0, 4.2e6 + 30.0]])         # extrémités à 42 m l'une de l'autre
    assert np.allclose(ouvert[0], ouvert[-1])                       # le piège
    assert not bruit.est_ferme(ouvert)
    ferme = np.vstack([ouvert[:3], ouvert[:1]])
    assert bruit.est_ferme(ferme)
    ferme[-1] += 1e-7
    assert bruit.est_ferme(ferme)
    ferme[-1] += 1e-3
    assert not bruit.est_ferme(ferme)
    # et la fabrication du trait ne referme pas la ligne ouverte de 42 m
    from fabrique import couches
    l = couches.tremble_ligne(LineString(ouvert), bandes.BANDES[4], "x", simplifier=False)
    assert not np.allclose(l.coords[0], l.coords[-1], atol=1.0, rtol=0)


@pytest.mark.parametrize("b", bandes.BANDES)
def test_trait_par_la_position_amplitude_entre_un_demi_et_un_et_demi_pixel(b):
    L = 700 * bandes.px(b)
    for decalage in (0.0, 1.0e6, 3.0e6, 7.0e6):
        x = np.linspace(decalage, decalage + L, 5)
        droite = np.column_stack([x, np.full(5, 5.0e5)])
        out = bruit.tremble_position(droite, bandes.amplitude(b), bandes.longueur_onde(b), 12345)
        crete = np.abs(out[:, 1] - 5.0e5).max() / bandes.px(b)
        assert 0.5 <= crete <= 1.5, (b, decalage, crete)


def test_trait_par_la_position_deux_morceaux_se_recollent_exactement():
    """Une courbe coupée à deux endroits différents donne, dans le recouvrement, exactement les mêmes sommets déplacés :
    c'est ce qui rend les cellules du relief indépendantes des blocs."""
    t = np.linspace(0, 1, 400)
    ligne = np.column_stack([4.0e6 + 60000 * t, 4.2e6 + 8000 * np.sin(6 * t)])
    amp, onde = 40.0, 3000.0
    a = bruit.tremble_position(ligne[:300], amp, onde, 7)
    b = bruit.tremble_position(ligne[100:], amp, onde, 7)
    # les sommets (densifiés et déplacés) communs aux deux morceaux : tous, sauf ceux des extrémités de chaque morceau
    da = {(round(float(x), 6), round(float(y), 6)) for x, y in a}
    db = {(round(float(x), 6), round(float(y), 6)) for x, y in b}
    commun = da & db
    assert len(commun) >= 190          # les 200 sommets du recouvrement, moins 1 à chaque bout (normales d'extrémité)


def test_trait_par_la_position_un_anneau_se_ferme_sans_precaution():
    t = np.linspace(0, 2 * np.pi, 60)
    anneau = np.column_stack([4.0e6 + 4000 * np.cos(t), 4.2e6 + 4000 * np.sin(t)])
    anneau[-1] = anneau[0]
    out = bruit.tremble_position(anneau, 30.0, 500.0, 3, ferme=True)
    assert np.allclose(out[0], out[-1]) and LineString(out).is_ring

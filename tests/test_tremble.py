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

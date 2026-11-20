"""Les reprises : une exception de GEOS dans une tuile se reprend, se compte et se nomme ; sans exception, rien ne change."""
import mapbox_vector_tile
import pytest
import shapely
from shapely.errors import GEOSException
from shapely.geometry import LineString, Polygon

from conftest import emprise_geojson, entrees_relief, osm_xml
from fabrique import fabriquer as fab, merc, mvt

Z, X, Y = 10, 600, 400
MSG = "TopologyException: side location conflict at 728 1623"


@pytest.fixture(autouse=True)
def reprises_vides():
    mvt.REPRISES.clear()
    yield
    mvt.REPRISES.clear()


def _m(xt, yt):
    """Un point en unités de tuile (0 à 4096) de la tuile Z/X/Y, en Mercator."""
    xmin, ymin, xmax, ymax = merc.bornes_tuile(Z, X, Y)
    t = xmax - xmin
    return (xmin + xt / merc.ETENDUE_MVT * t, ymax - yt / merc.ETENDUE_MVT * t)


def _poly(*pts):
    return Polygon([_m(*p) for p in pts])


def _carre(x0, y0, x1, y1):
    return _poly((x0, y0), (x1, y0), (x1, y1), (x0, y1))


def _entrees(*gs, props=None):
    return [(i, g, props or {}) for i, g in enumerate(gs)]


def _mer2():
    """Deux morceaux de mer qui se touchent, et une route."""
    return {"mer": _entrees(_carre(100, 100, 2000, 2000), _carre(2000, 100, 3000, 2000)),
            "routes": _entrees(LineString([_m(10, 10), _m(3000, 3500)]), props={"classe": "a"})}


def _decode(data):
    return mapbox_vector_tile.decode(data, default_options={"y_coord_down": True})


def _de_reference(par_couche):
    """Ce que l'encodeur faisait avant : les mêmes opérations, sans reprise."""
    xmin, ymin, xmax, ymax = merc.bornes_tuile(Z, X, Y)
    t = xmax - xmin
    k = merc.ETENDUE_MVT / t
    import numpy as np

    def vers(g):
        def f(c):
            out = np.empty_like(c)
            out[:, 0] = (c[:, 0] - xmin) * k
            out[:, 1] = (ymax - c[:, 1]) * k
            return out
        return shapely.set_precision(shapely.transform(g, f), 1.0)

    couches = []
    for nom in mvt.COUCHES:
        lst = par_couche.get(nom)
        if not lst:
            continue
        if nom == "mer":
            u = shapely.union_all([vers(g) for _, g, _ in lst], grid_size=1.0)
            parts = [p for p in (u.geoms if hasattr(u, "geoms") else [u]) if p.geom_type == "Polygon"
                     and not p.is_empty and p.area >= 0.5]
            parts.sort(key=lambda p: (p.bounds[0], p.bounds[1], p.area))
            feats = [{"geometry": p, "properties": {}} for p in parts]
        else:
            feats = [{"geometry": vers(g), "properties": dict(sorted(p.items()))} for _, g, p in lst]
        couches.append({"name": nom, "features": feats})
    return mapbox_vector_tile.encode(couches, default_options=mvt.OPTIONS)


def _leve(*a, **k):
    raise GEOSException(MSG)


def test_sans_panne_memes_octets_et_aucune_reprise():
    par = _mer2()
    assert mvt.encoder_tuile(Z, X, Y, par) == _de_reference(par)
    assert mvt.REPRISES == []
    assert mvt.ligne_reprises() is None


def test_un_morceau_de_mer_invalide_est_rendu_valide(monkeypatch):
    # set_precision laisse passer le nœud papillon tel quel : le morceau arrive invalide à l'union
    monkeypatch.setattr(shapely, "set_precision", lambda g, *a, **k: g)
    papillon = _poly((100, 100), (1000, 1000), (1000, 100), (100, 1000))
    assert not shapely.transform(papillon, lambda c: c).is_valid
    par = {"mer": _entrees(papillon)}
    data = mvt.encoder_tuile(Z, X, Y, par)
    assert data is not None
    mer = _decode(data)["mer"]["features"]
    assert len(mer) >= 1
    assert [r[3:] for r in mvt.REPRISES] == [("mer", "morceau revalidé")]
    assert mvt.REPRISES[0][:3] == (Z, X, Y)


def test_l_union_avec_grille_qui_leve_donne_l_union_sans_grille(monkeypatch):
    reel = shapely.union_all

    def union(g, *a, **k):
        if "grid_size" in k:
            raise GEOSException(MSG)
        return reel(g)

    monkeypatch.setattr(shapely, "union_all", union)
    par = _mer2()
    data = mvt.encoder_tuile(Z, X, Y, par)
    assert [r[3:] for r in mvt.REPRISES] == [("mer", "union sans grille")]
    assert len(_decode(data)["mer"]["features"]) == 1
    assert "routes" in _decode(data)


def test_les_deux_unions_qui_levent_laissent_les_morceaux_separes(monkeypatch):
    monkeypatch.setattr(shapely, "union_all", _leve)
    par = _mer2()
    data = mvt.encoder_tuile(Z, X, Y, par)
    assert [r[3:] for r in mvt.REPRISES] == [("mer", "morceaux séparés")]
    assert len(_decode(data)["mer"]["features"]) == 2


def test_set_precision_qui_leve_une_fois_puis_passe(monkeypatch):
    reel = shapely.set_precision
    n = []

    def sp(g, *a, **k):
        n.append(1)
        if len(n) == 1:
            raise GEOSException(MSG)
        return reel(g, *a, **k)

    monkeypatch.setattr(shapely, "set_precision", sp)
    par = {"routes": _entrees(LineString([_m(10, 10), _m(3000, 3500)]))}
    data = mvt.encoder_tuile(Z, X, Y, par)
    assert len(_decode(data)["routes"]["features"]) == 1
    assert [r[3:] for r in mvt.REPRISES] == [("routes", "arrondi après validation")]


def test_set_precision_qui_leve_toujours_ecarte_la_geometrie(monkeypatch):
    reel = shapely.set_precision

    def sp(g, *a, **k):
        if g.geom_type == "LineString" and not g.is_empty and g.length > 1000:
            raise GEOSException(MSG)
        return reel(g, *a, **k)

    monkeypatch.setattr(shapely, "set_precision", sp)
    par = {"routes": _entrees(LineString([_m(10, 10), _m(3000, 3500)])),
           "forets": _entrees(_carre(100, 100, 900, 900))}
    data = mvt.encoder_tuile(Z, X, Y, par)
    d = _decode(data)
    assert "routes" not in d and len(d["forets"]["features"]) == 1
    assert [r[3:] for r in mvt.REPRISES] == [("routes", "géométrie écartée")]


@pytest.mark.parametrize("ou", ["transform", "encode"])
def test_une_autre_erreur_nomme_la_tuile_et_la_couche(monkeypatch, ou):
    cause = ValueError("boum")

    def panne(*a, **k):
        raise cause

    if ou == "transform":
        monkeypatch.setattr(shapely, "transform", panne)
        attendu = "tuile %d/%d/%d, couche mer : " % (Z, X, Y)
    else:
        monkeypatch.setattr(mvt.mapbox_vector_tile, "encode", panne)
        attendu = "tuile %d/%d/%d, couche encodage : " % (Z, X, Y)
    with pytest.raises(RuntimeError) as e:
        mvt.encoder_tuile(Z, X, Y, _mer2())
    assert str(e.value).startswith(attendu)
    assert "ValueError: boum" in str(e.value)
    assert e.value.__cause__ is cause


def test_ligne_du_journal_au_plus_cinq_premieres():
    for i in range(7):
        mvt.REPRISES.append((10, i, 1, "mer", "union sans grille"))
    mvt.REPRISES.append((10, 9, 9, "routes", "géométrie écartée"))
    ligne = mvt.ligne_reprises()
    assert ligne.startswith("géométries reprises : 8 (union sans grille : 7, géométrie écartée : 1) ; premières : ")
    assert ligne.endswith("10/0/1 mer union sans grille, 10/1/1 mer union sans grille, 10/2/1 mer union sans grille, "
                          "10/3/1 mer union sans grille, 10/4/1 mer union sans grille")


def _fabriquer(tmp_path, journal):
    jeu = {"dossier": tmp_path}
    e = str(tmp_path / "e.geojson")
    emprise_geojson(e, (35.83, 35.89, 35.40, 35.46))
    p = str(tmp_path / "m.osm")
    osm_xml(p, dlon=-0.14)
    fab.fabriquer(e, [p], str(tmp_path / "c"), str(tmp_path / "sortie"), journal=journal,
                  entrees_relief=entrees_relief(jeu, "x", cote=35.87))


def test_fabriquer_sans_panne_aucune_ligne_de_reprises(tmp_path):
    lignes = []
    _fabriquer(tmp_path, lignes.append)
    assert lignes and not [l for l in lignes if l.startswith("géométries reprises")]


def test_fabriquer_avec_panne_sur_l_union_finit_et_le_dit(tmp_path, monkeypatch):
    reel = shapely.union_all

    def union(g, *a, **k):
        if "grid_size" in k:
            raise GEOSException(MSG)
        return reel(g, *a, **k)

    monkeypatch.setattr(shapely, "union_all", union)
    lignes = []
    _fabriquer(tmp_path, lignes.append)
    reprises = [l for l in lignes if l.startswith("géométries reprises")]
    assert len(reprises) == 1
    assert "union sans grille : " in reprises[0] and "; premières : " in reprises[0]
    assert (tmp_path / "sortie" / "fond.pmtiles").exists()

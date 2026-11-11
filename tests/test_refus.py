import pytest

from conftest import emprise_geojson
from fabrique import emprise, overpass
from fabrique.fabriquer import SortieErreur, fabriquer, verifier_dossier_sortie


def test_dossier_de_sortie_non_vide_refuse_sans_rien_ecrire(tmp_path):
    sortie = tmp_path / "sortie"
    sortie.mkdir()
    (sortie / "intrus.txt").write_text("là")
    e = tmp_path / "e.geojson"
    emprise_geojson(str(e))
    avant = sorted(p.name for p in tmp_path.rglob("*"))
    with pytest.raises(SortieErreur):
        verifier_dossier_sortie(str(sortie))
    with pytest.raises(SortieErreur):
        fabriquer(str(e), [str(e)], str(tmp_path / "cache"), str(sortie))
    assert sorted(p.name for p in tmp_path.rglob("*")) == avant
    assert (sortie / "intrus.txt").read_text() == "là"


def test_dossier_de_sortie_vide_ou_absent_accepte(tmp_path):
    verifier_dossier_sortie(str(tmp_path / "absent"))
    (tmp_path / "vide").mkdir()
    verifier_dossier_sortie(str(tmp_path / "vide"))


def test_emprise_overpass_trop_grande_refusee(tmp_path):
    from shapely.geometry import box
    # 3° x 3° : bien au-delà de 5 000 km2
    gros = box(35, 34, 38, 37)
    with pytest.raises(overpass.OverpassErreur):
        overpass.verifier_surface(gros)
    petit = box(35.9, 35.4, 36.0, 35.5)
    assert overpass.verifier_surface(petit) < 5000
    p = tmp_path / "gros.geojson"
    import json
    p.write_text(json.dumps({"type": "Polygon", "coordinates": [[[35, 34], [38, 34], [38, 37], [35, 37], [35, 34]]]}))
    appels = []
    with pytest.raises(overpass.OverpassErreur):
        overpass.extraire(str(p), str(tmp_path / "x.osm.gz"), ouvrir=lambda *a, **k: appels.append(1))
    assert not appels and not (tmp_path / "x.osm.gz").exists()


def test_emprise_doit_etre_un_polygone(tmp_path):
    with pytest.raises(emprise.EmpriseErreur):
        emprise.depuis_geojson({"type": "Point", "coordinates": [0, 0]})


def test_overpass_reessaie_sur_502_a_504_puis_renonce(tmp_path):
    import urllib.error
    e = tmp_path / "e.geojson"
    emprise_geojson(str(e))
    essais, pauses = [], []

    def ouvrir(req, timeout=None):
        essais.append(req.get_header("User-agent"))
        raise urllib.error.HTTPError(req.full_url, 502, "Bad Gateway", {}, None)

    with pytest.raises(overpass.OverpassErreur) as exc:
        overpass.extraire(str(e), str(tmp_path / "o.osm.gz"), ouvrir=ouvrir, dormir=pauses.append, journal=lambda *a: None)
    assert len(essais) == 5 and len(pauses) == 4 and all(30 <= p <= 45 for p in pauses)
    assert all("dcs-readyroom-basemaps" in ua for ua in essais)
    assert "5 fois" in str(exc.value)
    assert overpass.URL.startswith("http://")
    assert "village" not in overpass.requete((0, 0, 1, 1)) and "hamlet" not in overpass.requete((0, 0, 1, 1))


def test_le_refus_overpass_porte_sur_la_boite_pas_sur_le_polygone(tmp_path):
    """Un L fin : sa surface est de quelques centaines de km2, mais la boîte envoyée à Overpass fait 3° x 3°."""
    import json
    from shapely.geometry import Polygon
    ell = Polygon([(35, 34), (38, 34), (38, 34.05), (35.05, 34.05), (35.05, 37), (35, 37)])
    assert emprise.surface_km2(ell) < 5000                      # le polygone seul passerait
    with pytest.raises(overpass.OverpassErreur, match="boîte"):
        overpass.verifier_surface(ell)
    p = tmp_path / "l.geojson"
    p.write_text(json.dumps({"type": "Polygon", "coordinates": [[list(c) for c in ell.exterior.coords]]}))
    appels = []
    with pytest.raises(overpass.OverpassErreur):
        overpass.extraire(str(p), str(tmp_path / "x.osm.gz"), ouvrir=lambda *a, **k: appels.append(1))
    assert not appels


def test_une_emprise_au_sud_de_l_equateur_est_refusee_avec_un_message(tmp_path):
    e = tmp_path / "s.geojson"
    emprise_geojson(str(e), (35.0, 35.4, -1.0, 1.0))
    with pytest.raises(Exception, match="hémisphère nord"):
        fabriquer(str(e), [str(e)], str(tmp_path / "cache"), str(tmp_path / "sortie"))
    assert not (tmp_path / "sortie").exists()

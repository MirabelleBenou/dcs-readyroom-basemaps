from conftest import osm_xml
from fabrique import osm


def test_aucun_village_ni_hameau_ni_lieu_dit(tmp_path):
    p = str(tmp_path / "m.osm")
    osm_xml(p)
    d = osm.lire([p])
    assert sorted((l["place"], l["nom"]) for l in d.lieux) == [("city", "Ville"), ("town", "Bourg")]
    assert not any(l["place"] in ("village", "hamlet", "isolated_dwelling", "locality") for l in d.lieux)


def test_nom_en_usage_puis_name_en():
    assert osm.nom_en_usage({"name": "A", "name:en": "B"}) == "A"
    assert osm.nom_en_usage({"name:en": "B"}) == "B"
    assert osm.nom_en_usage({}) is None


def test_classes_de_routes_et_bretelles(tmp_path):
    p = str(tmp_path / "m.osm")
    osm_xml(p)
    d = osm.lire([p])
    assert sorted((c, l) for _, c, l, _ in d.routes) == [("motorway", False), ("motorway", True), ("primary", False), ("secondary", False)]
    assert len(d.pistes) == 1 and len(d.rail) == 1 and len(d.eau) == 1 and len(d.forets) == 1
    assert sorted(c for _, c, _ in d.cours_eau) == ["river", "stream"]


def test_frontieres_admin_level_2_seulement_sans_limites_maritimes(tmp_path):
    p = str(tmp_path / "m.osm")
    osm_xml(p)
    d = osm.lire([p])
    assert [i for i, _ in d.frontieres] == ["w13"]

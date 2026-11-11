from fabrique import sources


def test_nom_et_url_des_dalles():
    assert sources.nom_dalle(35, 35) == "N35_00_E035_00"
    assert sources.nom_dalle(-1, -2) == "S01_00_W002_00"
    assert sources.url_dalle(35, 36, sources.GLO30) == (
        "https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N35_00_E036_00_DEM/"
        "Copernicus_DSM_COG_10_N35_00_E036_00_DEM.tif")
    assert sources.url_dalle(40, 44, sources.GLO90).startswith(
        "https://copernicus-dem-90m.s3.amazonaws.com/Copernicus_DSM_COG_30_N40_00_E044_00_DEM/")


def test_dalles_necessaires_sans_debordement_a_la_borne_entiere():
    assert sources.dalles_necessaires((35.2, 35.2, 36.2, 35.7)) == [(35, 35), (35, 36)]
    assert sources.dalles_necessaires((35.0, 35.0, 36.0, 36.0)) == [(35, 35)]


def test_selection_glo30_repli_glo90_et_mer():
    """Sans réseau : une liste de dalles simulée."""
    glo30 = {(35, 35), (35, 36)}
    glo90 = {(40, 44)}
    sel = sources.choisir_dalles((35.2, 35.2, 36.2, 35.7), lambda la, lo: (la, lo) in glo30, lambda la, lo: (la, lo) in glo90)
    assert [(d["dalle"], d["source"]) for d in sel] == [("N35_00_E035_00", "glo30"), ("N35_00_E036_00", "glo30")]
    # l'Arménie et l'Azerbaïdjan : absentes du GLO-30, prises au GLO-90 ; la mer : absente des deux
    sel = sources.choisir_dalles((43.5, 39.5, 45.5, 40.5), lambda la, lo: False, lambda la, lo: (la, lo) in glo90)
    sources_ = {d["dalle"]: d["source"] for d in sel}
    assert sources_["N39_00_E043_00"] == "absente"
    assert sources_["N40_00_E044_00"] == "glo90"
    assert next(d for d in sel if d["source"] == "glo90")["url"].startswith("https://copernicus-dem-90m")
    assert all(d["url"] is None for d in sel if d["source"] == "absente")


def test_dalle_absente_donne_altitude_zero(tmp_path):
    import numpy as np
    from fabrique import terrain
    g = terrain.Grille((3.9e6, 4.2e6, 3.91e6, 4.21e6))
    dem = terrain.construire_dem(g, [{"dalle": "N35_00_E035_00", "lat": 35, "lon": 35, "source": "absente", "adresse": None}])
    assert dem.shape == g.forme and float(np.abs(dem).max()) == 0.0

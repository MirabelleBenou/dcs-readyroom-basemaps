import json


from fabrique import licence, sprite, style

ATT = licence.attributions_courtes([{"source": "glo30"}])


def les_styles():
    return {"clair": style.construire(style.CLAIR, ATT), "sombre": style.construire(style.SOMBRE, ATT)}


def test_urls_relatives_au_style():
    for s in les_styles().values():
        assert s["sources"]["fond"]["url"] == "pmtiles://./fond.pmtiles"
        assert s["sources"]["relief"]["url"] == "pmtiles://./relief.pmtiles"
        assert s["sprite"] == "./sprite/papier"
        assert s["glyphs"] == "./glyphs/{fontstack}/{range}.pbf"
        txt = json.dumps(s)
        assert "http" not in txt.replace("pmtiles://", "") and "/home" not in txt


def test_ordre_des_couches_de_papier_py():
    s = les_styles()["clair"]
    ids = [l["id"] for l in s["layers"]]
    assert len(ids) == len(set(ids))

    def pos(prefixe):
        return min(i for i, x in enumerate(ids) if x.startswith(prefixe))
    ordre = ["papier", "relief", "mer", "rivage", "courbes", "forets", "eau", "cours-eau", "localites", "routes",
             "rail", "pistes", "frontieres", "grille", "noms"]
    positions = [pos(o) for o in ordre]
    assert positions == sorted(positions)
    assert s["layers"][0]["paint"]["background-pattern"] == "papier"


def test_le_sombre_ne_change_que_le_style():
    c, s = les_styles()["clair"], les_styles()["sombre"]
    assert c["sources"].keys() == s["sources"].keys()
    assert [l["id"] for l in c["layers"]] == [l["id"] for l in s["layers"]]
    assert s["layers"][0]["paint"]["background-pattern"] == "papier-sombre"
    relief = next(l for l in s["layers"] if l["id"] == "relief")["paint"]
    assert relief["raster-brightness-min"] == 1 and relief["raster-brightness-max"] == 0
    for nom, p in (("papier", "#24211c"), ("encre", "#d8ccb4"), ("bleu", "#7f9db5"), ("brique", "#c07a5e"), ("brun", "#a88a6c"),
                   ("vert", "#8a9e78"), ("courbes", "#9a8366"), ("grille", "#8aa0b0"), ("mer", "#1f2a2c"),
                   ("lacs", "#28393b"), ("frontieres", "#a885a8")):
        assert style.SOMBRE[nom] == p
    for nom, p in (("papier", "#f3ebd8"), ("mer", "#dfe5df"), ("lacs", "#cdd9d6"), ("courbes", "#8a6a48"),
                   ("frontieres", "#6b3d6b"), ("brun", "#8c6b4e")):
        assert style.CLAIR[nom] == p


def test_le_fond_se_tait_saturation_hsl_sous_60_pour_cent():
    for s in les_styles().values():
        assert style.sature(s) == []
        assert max(style.saturation_hsl(*c) for _, c in style.couleurs(s)) <= 0.60
    # le détecteur voit bien une couleur trop vive
    assert style.sature({"x": "#ff0000"}) and style.sature({"x": "rgb(10, 200, 20)"})


def test_images_du_sprite_toutes_presentes():
    noms = {n for n, _ in sprite.images(1)}
    assert noms == {n for n, _ in sprite.images(2)}
    for s in les_styles().values():
        for l in s["layers"]:
            for cle in ("icon-image",):
                v = l.get("layout", {}).get(cle)
                if v:
                    assert v in noms
            for cle in ("background-pattern", "fill-pattern", "line-pattern"):
                v = l.get("paint", {}).get(cle)
                if v:
                    assert v in noms
    assert {"papier", "papier-sombre"} <= noms


def test_les_noms_de_polices_sont_ceux_des_glyphes():
    from fabrique import glyphes
    polices = {n for n, _ in glyphes.POLICES}
    for s in les_styles().values():
        for l in s["layers"]:
            for f in l.get("layout", {}).get("text-font", []):
                assert f in polices


def test_les_trois_classes_de_routes_seulement():
    s = les_styles()["clair"]
    classes = set()
    for l in s["layers"]:
        if l.get("source-layer") == "routes":
            classes.add(l["filter"][2])
    assert classes == {"secondary", "primary", "trunk", "motorway"}

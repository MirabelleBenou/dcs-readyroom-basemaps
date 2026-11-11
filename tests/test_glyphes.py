import os

from fabrique import glyphes

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def varint(b, i):
    n = s = 0
    while True:
        c = b[i]
        i += 1
        n |= (c & 0x7F) << s
        if not c & 0x80:
            return n, i
        s += 7


def champs(b):
    i = 0
    while i < len(b):
        cle, i = varint(b, i)
        num, typ = cle >> 3, cle & 7
        if typ == 0:
            v, i = varint(b, i)
        elif typ == 2:
            n, i = varint(b, i)
            v = b[i:i + n]
            i += n
        else:
            raise AssertionError(typ)
        yield num, v


def test_plage_decodable_et_glyphes_coherents():
    data = glyphes.fabriquer_plage(os.path.join(RACINE, "polices", "SpecialElite-Regular.ttf"), "Special Elite", 0, 255)
    pile = next(v for n, v in champs(data) if n == 1)
    c = list(champs(pile))
    assert [v for n, v in c if n == 1] == [b"Special Elite"] and [v for n, v in c if n == 2] == [b"0-255"]
    gl = {}
    for n, v in c:
        if n == 3:
            g = dict(champs(v))
            gl[g[1]] = g
    assert 65 in gl and 97 in gl and 32 in gl
    a = gl[65]
    assert len(a[2]) == (a[3] + 6) * (a[4] + 6) and a[7] > 0
    assert 2 not in gl[32]          # l'espace n'a pas d'image


def test_les_plages_demandees_existent_meme_sans_glyphe(tmp_path):
    glyphes.fabriquer_glyphes(os.path.join(RACINE, "polices"), str(tmp_path))
    for police in ("Special Elite", "JetBrains Mono"):
        for deb in range(0, 1280, 256):
            assert (tmp_path / "glyphs" / police / ("%d-%d.pbf" % (deb, deb + 255))).stat().st_size > 0
    # JetBrains Mono porte le cyrillique et le grec ; Special Elite n'a que le latin
    data = (tmp_path / "glyphs" / "JetBrains Mono" / "1024-1279.pbf").read_bytes()
    pile = next(v for n, v in champs(data) if n == 1)
    assert sum(1 for n, v in champs(pile) if n == 3) > 100


def test_la_pile_des_noms_porte_le_nom_que_maplibre_demande(tmp_path):
    """MapLibre demande glyphs/<polices jointes par une virgule>/<plage>.pbf ; chaque glyphe vient de Special Elite s'il
    l'a, sinon de JetBrains Mono."""
    assert glyphes.NOM_PILE == "Special Elite,JetBrains Mono"
    polices = glyphes.charger_polices(os.path.join(RACINE, "polices"))
    glyphes.fabriquer_glyphes(os.path.join(RACINE, "polices"), str(tmp_path), polices)
    d = tmp_path / "glyphs" / "Special Elite,JetBrains Mono"
    assert d.is_dir()
    # les plages du cadrage, et toute plage où une des deux polices a un glyphe
    for deb in range(0, 1280, 256):
        assert (d / ("%d-%d.pbf" % (deb, deb + 255))).stat().st_size > 0
    codes = {c // 256 * 256 for c in polices["JetBrains Mono"].cmap} | {c // 256 * 256 for c in polices["Special Elite"].cmap}
    assert {int(f.name.split("-")[0]) for f in d.iterdir()} >= codes

    def glyphes_de(nom_fichier):
        data = (d / nom_fichier).read_bytes()
        pile = next(v for n, v in champs(data) if n == 1)
        c = list(champs(pile))
        assert [v for n, v in c if n == 1] == [b"Special Elite,JetBrains Mono"]
        return {dict(champs(v))[1]: dict(champs(v)) for n, v in c if n == 3}

    g = glyphes_de("256-511.pbf")                       # le latin étendu : ş (351) et ğ (287)
    assert 0x15f in g and 0x11f in g
    # ş vient de JetBrains Mono (Special Elite ne l'a pas) ; "A" (65) vient de Special Elite, qui l'a
    mono, elite = polices["JetBrains Mono"], polices["Special Elite"]
    assert 0x15f not in elite.cmap and 0x15f in mono.cmap
    g0 = glyphes_de("0-255.pbf")
    assert dict(champs(elite.glyphe(65)))[7] == g0[65][7]
    assert dict(champs(mono.glyphe(0x15f)))[7] == g[0x15f][7]


def test_un_caractere_absent_des_deux_polices_se_tait():
    polices = glyphes.charger_polices(os.path.join(RACINE, "polices"))
    assert glyphes.pile_couvre(polices, "Latakia")
    assert glyphes.pile_couvre(polices, "Şağlam")                       # Şağlam : JetBrains Mono
    assert not glyphes.pile_couvre(polices, "თბილისი")      # géorgien : ni l'une ni l'autre
    assert not glyphes.pile_couvre(polices, "Tbilisi თ")
    assert glyphes.pile_couvre(polices, "")


def test_les_trois_dossiers_de_glyphes(tmp_path):
    glyphes.fabriquer_glyphes(os.path.join(RACINE, "polices"), str(tmp_path))
    assert sorted(p.name for p in (tmp_path / "glyphs").iterdir()) == ["JetBrains Mono", "Special Elite",
                                                                       "Special Elite,JetBrains Mono"]

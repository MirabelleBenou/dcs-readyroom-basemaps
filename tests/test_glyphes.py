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

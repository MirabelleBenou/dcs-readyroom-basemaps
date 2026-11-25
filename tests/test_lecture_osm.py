"""Le temps de lecture de chaque extrait : `Lecture.extraits`, `relations_s`, `recollage_s`, au journal, au manifeste
(`execution.osm`, hors de la partie comparable) et dans la ligne de `verifier.controles`."""
import json
import os
import re
import shutil

from conftest import LAT0, LAT1, LON0, LON1
from fabrique import manifeste, merc, osm
from fabrique.couches import ENTITES
from fabrique.magasin import Magasin
from fabrique.verifier import controles
from shapely.geometry import box

from test_exclusions import fabrique_extrait
from test_mini import deux  # noqa: F401  (la fixture : deux fabrications du jeu minuscule)


def lire_deux(tmp_path, exclusions):
    fichiers = [fabrique_extrait(tmp_path, "a.osm"), fabrique_extrait(tmp_path, "b.osm")]
    x0, y0 = merc.lonlat_vers_merc(LON0 - 0.5, LAT0 - 0.5)
    x1, y1 = merc.lonlat_vers_merc(LON1 + 0.5, LAT1 + 0.5)
    travail = tmp_path / "t"
    os.makedirs(str(travail), exist_ok=True)
    mg = Magasin(os.path.join(str(travail), "m.sqlite"))
    lignes = []
    try:
        lecture = osm.lire(fichiers, mg, box(float(x0), float(y0), float(x1), float(y1)), str(travail),
                           journal=lignes.append, exclusions=exclusions)
    finally:
        mg.fermer()
    return lecture, lignes


def test_extraits_dans_l_ordre_avec_les_secondes_du_journal(tmp_path):
    lecture, lignes = lire_deux(tmp_path, None)
    assert [n for n, _ in lecture.extraits] == ["a.osm", "b.osm"]
    assert all(isinstance(s, int) and s >= 0 for _, s in lecture.extraits)
    vus = [(m.group(1), int(m.group(2))) for m in (re.fullmatch(r"OpenStreetMap lu : (.+) \(\d/2\) en (\d+) s", l)
                                                   for l in lignes) if m]
    assert vus == [tuple(e) for e in lecture.extraits]
    assert lecture.relations_s is None
    assert not [l for l in lignes if l.startswith("relations des frontières lues en")]
    assert isinstance(lecture.recollage_s, int) and lecture.recollage_s >= 0


def test_relations_s_entier_avec_exclusions_et_au_journal(tmp_path):
    lecture, lignes = lire_deux(tmp_path, {"autoroutes": [{"ref": "A 17"}]})
    assert isinstance(lecture.relations_s, int) and lecture.relations_s >= 0
    i = [k for k, l in enumerate(lignes) if l == "relations des frontières lues en %d s" % lecture.relations_s]
    assert len(i) == 1
    assert i[0] < min(k for k, l in enumerate(lignes) if l.startswith("OpenStreetMap lu :"))
    assert isinstance(lecture.recollage_s, int)


def _avec_osm(dossier_source, dest, osm_manifeste):
    """Copie le dossier rendu et pose (ou retire, si None) `execution.osm` dans son manifeste."""
    shutil.copytree(dossier_source, dest)
    chemin = os.path.join(dest, "manifeste.json")
    with open(chemin, encoding="utf-8") as f:
        m = json.load(f)
    if osm_manifeste is None:
        m["execution"].pop("osm", None)
    else:
        m["execution"]["osm"] = osm_manifeste
    manifeste.ecrire(chemin, m)
    return dest


def test_ligne_de_verifier_a_l_octet_et_absente_sans_la_cle(deux, tmp_path):  # noqa: F811
    avec = _avec_osm(deux[0], str(tmp_path / "avec"), {"extraits": [["a.pbf", 12], ["b.pbf", 3]], "relations_s": 7,
                                                       "recollage_s": 5})
    lignes = []
    ok, _ = controles(avec, journal=lignes.append)
    assert ok
    assert [l for l in lignes if l.startswith("lecture d'OSM")] == [
        "lecture d'OSM : a.pbf 12 s, b.pbf 3 s ; relations 7 s ; recollage 5 s ; total 27 s"]
    assert lignes.index([l for l in lignes if l.startswith("lecture d'OSM")][0]) == \
        [k for k, l in enumerate(lignes) if l.startswith("le fond se tait")][0] + 1
    sans_rel = _avec_osm(deux[0], str(tmp_path / "sans_rel"), {"extraits": [["a.pbf", 12], ["b.pbf", 3]], "relations_s": None,
                                                              "recollage_s": 5})
    lignes = []
    controles(sans_rel, journal=lignes.append)
    assert [l for l in lignes if l.startswith("lecture d'OSM")] == [
        "lecture d'OSM : a.pbf 12 s, b.pbf 3 s ; relations - s ; recollage 5 s ; total 20 s"]
    # sans la clé : aucune ligne, et le même résultat que sur le dossier d'origine privé de la clé
    sans = _avec_osm(deux[0], str(tmp_path / "sans"), None)
    lignes = []
    ok_s, res_s = controles(sans, journal=lignes.append)
    assert ok_s and not [l for l in lignes if l.startswith("lecture d'OSM")]
    assert res_s["echecs"] == [] and "avertissements" not in res_s


def test_execution_osm_au_manifeste_hors_de_la_partie_comparable(deux):  # noqa: F811
    ms = [json.load(open(os.path.join(d, "manifeste.json"), encoding="utf-8")) for d in deux]
    for m in ms:
        o = m["execution"]["osm"]
        assert [n for n, _ in o["extraits"]] == ["mini.osm"]
        assert o["relations_s"] is None and isinstance(o["recollage_s"], int)
        assert "execution" not in manifeste.partie_comparable(m)
    assert manifeste.sha256_comparable(ms[0]) == manifeste.sha256_comparable(ms[1])

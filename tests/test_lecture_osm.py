"""Le temps de lecture de chaque extrait : `Lecture.extraits`, `relations_s`, `recollage_s` (le manifeste et la ligne de
`verifier.controles` sont dans `test_mini.py`)."""
import os
import re

from conftest import LAT0, LAT1, LON0, LON1
from fabrique import merc, osm
from fabrique.magasin import Magasin
from shapely.geometry import box

from test_exclusions import fabrique_extrait


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
    assert isinstance(lecture.recollage_s, int) and lecture.recollage_s >= 0


def test_relations_s_entier_avec_exclusions_et_au_journal(tmp_path):
    lecture, lignes = lire_deux(tmp_path, {"autoroutes": [{"ref": "A 17"}]})
    assert isinstance(lecture.relations_s, int) and lecture.relations_s >= 0
    assert not [l for l in lignes if "relations" in l and "lues" in l]      # aucune ligne neuve au journal
    assert isinstance(lecture.recollage_s, int)

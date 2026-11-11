"""Le dépôt est public : aucune référence privée (organisation, personne, dépôt privé), rien de lourd, aucune tuile."""
import os
import re
import shutil
import subprocess

import pytest

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# les mots sont coupés : ce fichier ne doit pas se déclencher lui-même
MOTS = ["k" + "hr", "ve" + "aw", "esca" + "dr", "squa" + "dron", "ben" + "oit", "readyroom-" + "lots", "ready " + "room"]
INTERDIT = re.compile("|".join(MOTS), re.I)


def suivis():
    out = subprocess.run(["git", "-C", RACINE, "ls-files"], capture_output=True, text=True, check=True).stdout.split("\n")
    return [f for f in out if f and os.path.isfile(os.path.join(RACINE, f))]


@pytest.mark.skipif(shutil.which("git") is None or not os.path.isdir(os.path.join(RACINE, ".git")), reason="pas un clone git")
def test_aucune_reference_privee_ni_fichier_lourd_ni_tuile():
    for f in suivis():
        p = os.path.join(RACINE, f)
        assert os.path.getsize(p) <= 2 * 1024 * 1024, f
        assert not f.endswith((".pmtiles", ".tif", ".osm", ".osm.gz", ".osm.pbf")), f
        if f.endswith((".ttf", ".png", ".webp", ".pbf")):
            continue
        with open(p, encoding="utf-8", errors="replace") as h:
            for n, ligne in enumerate(h, 1):
                assert not INTERDIT.search(ligne), "%s:%d" % (f, n)
    assert not os.path.exists(os.path.join(RACINE, "dist"))

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
# le seul nom de produit permis : celui de l'application publique (casse comprise)
NOM_PERMIS = "Squa" + "dron Ready " + "Room"


def ligne_interdite(ligne):
    """Vrai si la ligne porte un mot interdit, une fois retiré chaque nom de produit permis (casse comprise)."""
    return INTERDIT.search(ligne.replace(NOM_PERMIS, "")) is not None


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
                assert not ligne_interdite(ligne), "%s:%d" % (f, n)
    assert not os.path.exists(os.path.join(RACINE, "dist"))


def test_seul_le_nom_du_produit_est_permis():
    sq, rr = "squa" + "dron", "ready " + "room"
    for texte in (sq, sq.capitalize(), (sq + " " + rr).upper(), rr, "the " + NOM_PERMIS + " " + sq):
        assert ligne_interdite(texte), texte
    for texte in (NOM_PERMIS, "part of the " + NOM_PERMIS + " family"):
        assert not ligne_interdite(texte), texte

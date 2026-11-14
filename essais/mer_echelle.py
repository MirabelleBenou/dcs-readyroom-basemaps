"""La mer à l'échelle d'un théâtre : l'étape de la mer seule, nourrie bande par bande par une grille synthétique du zoom 10.

Usage : python3.12 essais/mer_echelle.py [hauteur largeur [hauteur_de_bande]]     (16 000 x 14 000 et 64 lignes par défaut)

À lancer dans son propre processus : la mémoire se mesure par `ru_maxrss` (le plus haut du processus) : l'augmentation est
celle du pic au-dessus du pic du même processus après ses imports.
"""
import os
import resource
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mer_synthetique import MerSynthetique  # noqa: E402
from fabrique.terrain import MerParBandes  # noqa: E402


def mio():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def principal(argv=None):
    a = [int(x) for x in (argv if argv is not None else sys.argv[1:])]
    haut, larg, bande = a + [14000, 16000, 64][len(a):]
    base = mio()
    t0 = time.time()
    g = MerSynthetique(haut, larg)
    mer = MerParBandes(haut, larg, g.dedans)
    for i0 in range(0, haut, bande):
        i1 = min(haut, i0 + bande)
        b, gr = g.bande(i0, i1)
        mer.ajouter(i0, b, gr)
    t1 = time.time()
    mer.resoudre()
    t2 = time.time()
    n = mer.compte()
    for i0 in range(0, haut, 256):
        mer.bande(i0, min(haut, i0 + 256))
    pic = mio()
    print("grille %d x %d (%.0f Mpixels), bandes de %d lignes" % (larg, haut, haut * larg / 1e6, bande))
    print("pixels de mer : %d" % n)
    print("durée : génération et rangement %.1f s, résolution %.1f s, total %.1f s" % (t1 - t0, t2 - t1, time.time() - t0))
    print("ru_maxrss : %.0f Mio après les imports, %.0f Mio au plus haut : augmentation %.0f Mio" % (base, pic, pic - base))
    return 0


if __name__ == "__main__":
    sys.exit(principal())

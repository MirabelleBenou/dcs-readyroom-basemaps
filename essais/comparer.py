"""Compare deux dossiers produits : sha256 de chaque fichier ; le manifeste par sa partie comparable (sans `fabrique_le`).

Usage : python3.12 essais/comparer.py <dossier A> <dossier B>     (code 0 si identiques)
"""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fabrique.manifeste import sha256_comparable  # noqa: E402


def empreintes(dossier):
    out = {}
    for racine, _, fichiers in os.walk(dossier):
        for f in fichiers:
            chemin = os.path.join(racine, f)
            rel = os.path.relpath(chemin, dossier).replace(os.sep, "/")
            if rel == "manifeste.json":
                with open(chemin, encoding="utf-8") as h:
                    out[rel] = sha256_comparable(json.load(h))
            else:
                with open(chemin, "rb") as h:
                    out[rel] = hashlib.sha256(h.read()).hexdigest()
    return out


def principal(argv=None):
    a, b = (argv if argv is not None else sys.argv[1:])[:2]
    ea, eb = empreintes(a), empreintes(b)
    for k in sorted(set(ea) | set(eb)):
        etat = "identique" if ea.get(k) == eb.get(k) else "DIFFÈRE"
        print("%-9s %s  %s" % (etat, ea.get(k, "-"), k))
    return 0 if ea == eb else 1


if __name__ == "__main__":
    sys.exit(principal())

"""Les trois commandes : extraire-overpass, fabriquer, verifier."""
import argparse
import sys


def principal(argv=None):
    p = argparse.ArgumentParser(prog="fabrique", description="Fabrique des fonds de carte (style papier).")
    p.add_argument("--debug", action="store_true", help="affiche la trace complète des erreurs")
    sp = p.add_subparsers(dest="cmd", required=True)

    a = sp.add_parser("extraire-overpass", help="rapatrie une petite emprise (OpenStreetMap) dans un .osm.gz")
    a.add_argument("--emprise", required=True)
    a.add_argument("--sortie", required=True)

    b = sp.add_parser("fabriquer", help="fabrique le dossier servable")
    b.add_argument("--emprise", required=True)
    b.add_argument("--osm", required=True, nargs="+")
    b.add_argument("--cache-relief", required=True)
    b.add_argument("--sortie", required=True)
    b.add_argument("--bloc-zoom", type=int, default=8, choices=range(7, 11), metavar="7..10",
                   help="zoom pivot des blocs (7 à 10, 8 par défaut) : plus il est haut, moins la mémoire est sollicitée ; "
                        "le résultat est le même")

    c = sp.add_parser("verifier", help="rejoue les contrôles sur un dossier produit")
    c.add_argument("dossier")

    args = p.parse_args(argv)
    try:
        if args.cmd == "extraire-overpass":
            from .overpass import extraire
            n = extraire(args.emprise, args.sortie)
            print("ok : %d octets de XML, écrits dans %s" % (n, args.sortie))
        elif args.cmd == "fabriquer":
            from .fabriquer import fabriquer
            fabriquer(args.emprise, args.osm, args.cache_relief, args.sortie, bloc_zoom=args.bloc_zoom)
        else:
            from .verifier import verifier
            return 0 if verifier(args.dossier) else 1
    except Exception as e:  # message net ; code 1 pour `verifier` (toute erreur), 2 pour les autres commandes
        print("erreur : %s" % e, file=sys.stderr)
        if args.debug:
            raise
        return 1 if args.cmd == "verifier" else 2
    return 0

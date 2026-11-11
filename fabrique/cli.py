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
            fabriquer(args.emprise, args.osm, args.cache_relief, args.sortie)
        else:
            from .verifier import verifier
            return 0 if verifier(args.dossier) else 1
    except Exception as e:  # message net, code 2
        print("erreur : %s" % e, file=sys.stderr)
        if args.debug:
            raise
        return 2
    return 0

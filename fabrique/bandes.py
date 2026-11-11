"""Les bandes de zoom du trait : chaque bande a sa géométrie (simplification et tremblement propres)."""
from . import merc

BANDES = ((5, 6), (7, 8), (9, 10), (11, 12), (13, 14))
ZOOM_MIN_FOND = 5
ZOOM_MAX_FOND = 14
ZOOM_MIN_RELIEF = 5
ZOOM_MAX_RELIEF = 12

AMPLITUDE_PX = 1.0        # amplitude du tremblement, en pixels du zoom médian de la bande
ONDE_PX = 70.0            # longueur d'onde, idem  (le set_sketch_params(1.0, 70, 2) de papier.py)
TOLERANCE_PX = 0.5        # Douglas-Peucker : un demi-pixel de la bande
ESPACE_ARBRES_PX = 24.0   # espacement des arbres des forêts, en pixels de la bande


def bande_de(z):
    for b in BANDES:
        if b[0] <= z <= b[1]:
            return b
    raise ValueError("zoom hors des bandes : %r" % (z,))


def zoom_median(b):
    return (b[0] + b[1]) / 2.0


def px(b):
    """Taille d'un pixel d'écran au zoom médian de la bande, en mètres Mercator."""
    return merc.pixel(zoom_median(b))


def amplitude(b):
    return AMPLITUDE_PX * px(b)


def longueur_onde(b):
    return ONDE_PX * px(b)


def tolerance(b):
    return TOLERANCE_PX * px(b)


def distances_rivage(b):
    """250, 650, 1 250 et 2 100 m aux bandes 11-12 et 13-14 ; doublées à chaque bande plus basse."""
    base = (250, 650, 1250, 2100)
    facteur = {(13, 14): 1, (11, 12): 1, (9, 10): 2, (7, 8): 4, (5, 6): 8}[b]
    return tuple(d * facteur for d in base)


def niveaux_courbes(b, alt_max):
    """1 000 m seules aux bandes 5-6 et 7-8, puis toutes les 200 m (maîtresses à 1 000 m)."""
    pas = 1000 if b[0] <= 7 else 200
    n = int(alt_max // pas) + 1
    return [pas * i for i in range(1, n + 1)]


def liens_routes(b):
    """Les bretelles `*_link` suivent leur classe et disparaissent sous la bande 11-12."""
    return b[0] >= 11


def pas_grille(b):
    """Lignes de 100 km aux bandes 5-6 et 7-8, de 10 km ensuite."""
    return 100000 if b[0] <= 7 else 10000


def carres_100km(b):
    return b[0] >= 9

"""Aléa stable et trait tremblé.

Tout aléa vient d'une graine tirée de l'entité (son identifiant OSM, ou les coordonnées arrondies d'une entité
calculée) : jamais de l'heure, jamais de l'ordre de lecture.
"""
import hashlib
import math

import numpy as np


def graine(*parts):
    """Entier stable (64 bits) tiré d'une suite de valeurs."""
    h = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).digest()
    return int.from_bytes(h[:8], "big")


def graine_coords(*coords, pas=1.0):
    """Graine d'une entité calculée : ses coordonnées arrondies au pas (mètres)."""
    return graine(*[int(round(c / pas)) for c in coords])


def _splitmix(x):
    x = (x + np.uint64(0x9E3779B97F4A7C15))
    x = (x ^ (x >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
    x = (x ^ (x >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
    return x ^ (x >> np.uint64(31))


def hasard_cellules(ix, iy, sel):
    """Deux tirages uniformes [0,1) par cellule (ix, iy), vectorisés et sans état."""
    with np.errstate(over="ignore"):
        k = (ix.astype(np.int64).astype(np.uint64) * np.uint64(0x9E3779B185EBCA87)
             ^ iy.astype(np.int64).astype(np.uint64) * np.uint64(0xC2B2AE3D27D4EB4F)
             ^ np.uint64(sel))
        a = _splitmix(k)
        b = _splitmix(a)
    u = (a >> np.uint64(11)).astype(np.float64) / float(1 << 53)
    v = (b >> np.uint64(11)).astype(np.float64) / float(1 << 53)
    return u, v


def _lisse_pas(t):
    return t * t * (3 - 2 * t)


def _normales(c, ferme):
    """Normales unitaires de chaque sommet (différences centrales)."""
    if ferme:
        d = np.roll(c, -1, axis=0) - np.roll(c, 1, axis=0)
    else:
        d = np.empty_like(c)
        d[1:-1] = c[2:] - c[:-2]
        d[0] = c[1] - c[0]
        d[-1] = c[-1] - c[-2]
    n = np.hypot(d[:, 0], d[:, 1])
    n[n == 0] = 1.0
    return np.column_stack([-d[:, 1] / n, d[:, 0] / n])


POIDS = (0.65, 0.35)       # somme = 1 : l'amplitude crête du déplacement ne dépasse jamais `amplitude`
RAPPORT_ONDES = 2.3         # la seconde sinusoïde a une longueur d'onde plus courte


def tremble(coords, seed, amplitude, longueur_onde, ferme=False):
    """Densifie puis déplace chaque sommet perpendiculairement à la ligne d'un bruit lisse.

    coords : (n, 2) en mètres ; ferme : l'anneau se referme (premier = dernier point d'entrée, conservé).
    amplitude et longueur_onde en mètres (déjà convertis depuis les pixels du zoom médian de la bande).
    Une ligne ouverte garde ses extrémités fixes (le déplacement s'efface sur une demi-longueur d'onde) :
    les chemins qui se touchent restent reliés.
    """
    c = np.asarray(coords, dtype="float64")
    if ferme and np.allclose(c[0], c[-1]):
        c = c[:-1]
    if len(c) < 2 or amplitude <= 0:
        return np.vstack([c, c[:1]]) if ferme else c
    pas_max = longueur_onde / 12.0
    seg = np.diff(np.vstack([c, c[:1]]) if ferme else c, axis=0)
    ls = np.hypot(seg[:, 0], seg[:, 1])
    n_sub = np.maximum(1, np.ceil(ls / pas_max).astype(int))
    pts = []
    for i in range(len(seg)):
        a = c[i]
        t = (np.arange(n_sub[i]) / n_sub[i])[:, None]
        pts.append(a + t * seg[i])
    if not ferme:
        pts.append(c[-1:])
    d = np.vstack(pts)
    # abscisse curviligne
    nxt = np.roll(d, -1, axis=0) if ferme else d[1:]
    base = d if ferme else d[:-1]
    pas = np.hypot(*(nxt - base).T)
    s = np.concatenate([[0.0], np.cumsum(pas)])
    longueur = s[-1] if not ferme else s[-1]           # pour un anneau, s[-1] = périmètre
    s = s[:len(d)]
    rng = np.random.default_rng(seed)
    ph = rng.uniform(0, 2 * math.pi, 2)
    l1, l2 = longueur_onde, longueur_onde / RAPPORT_ONDES
    if ferme:    # ondes périodiques : un nombre entier de périodes sur le périmètre
        l1 = longueur / max(1, round(longueur / l1))
        l2 = longueur / max(1, round(longueur / l2))
    f = POIDS[0] * np.sin(2 * math.pi * s / l1 + ph[0]) + POIDS[1] * np.sin(2 * math.pi * s / l2 + ph[1])
    f *= amplitude
    if not ferme and longueur > 0:
        ext = np.minimum(s, longueur - s) / (longueur_onde / 2.0)
        f *= _lisse_pas(np.clip(ext, 0, 1))
    out = d + _normales(d, ferme) * f[:, None]
    if ferme:
        out = np.vstack([out, out[:1]])
    return out

"""Glyphes MapLibre (PBF, SDF 24 px) fabriqués depuis les polices TrueType du dépôt : sans Node, sans fontnik."""
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from fontTools.ttLib import TTFont
from scipy import ndimage

TAILLE = 24            # pixels d'une em
TAMPON = 3             # marge du SDF autour du glyphe
RAYON = 8.0
COUPURE = 0.25
ASCENDANT = 26         # constante du décalage vertical (comme les piles de glyphes habituelles de MapLibre)
PLAGES = tuple((i, i + 255) for i in range(0, 1280, 256))          # 0-255 ... 1024-1279
POLICES = (("Special Elite", "SpecialElite-Regular.ttf"), ("JetBrains Mono", "JetBrainsMono-Regular.ttf"))


def _varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _zigzag(n):
    return (n << 1) ^ (n >> 63) if n < 0 else n << 1


def _champ_varint(num, val):
    return _varint(num << 3 | 0) + _varint(val)


def _champ_octets(num, data):
    return _varint(num << 3 | 2) + _varint(len(data)) + data


def _glyphe_pb(code, bitmap, larg, haut, gauche, haut_px, avance):
    g = _champ_varint(1, code)
    if bitmap is not None:
        g += _champ_octets(2, bitmap)
    g += _champ_varint(3, larg) + _champ_varint(4, haut) + _champ_varint(5, _zigzag(gauche))
    g += _champ_varint(6, _zigzag(haut_px)) + _champ_varint(7, avance)
    return g


def sdf(masque):
    """Distance signée (positive hors du glyphe) en pixels, puis octets comme fontnik."""
    dedans = masque > 127
    d_hors = ndimage.distance_transform_edt(~dedans)
    d_dans = ndimage.distance_transform_edt(dedans)
    d = np.where(dedans, -(d_dans - 0.5), d_hors - 0.5)
    v = 255.0 * (1.0 - (d / RAYON + COUPURE))
    return np.clip(np.rint(v), 0, 255).astype("uint8")


def glyphe(font, code):
    ch = chr(code)
    avance = int(round(font.getlength(ch)))
    masque = font.getmask(ch, mode="L")
    larg, haut = masque.size
    if larg == 0 or haut == 0:
        return _glyphe_pb(code, None, 0, 0, 0, 0, avance)
    gauche, haut_px, _, _ = font.getbbox(ch, anchor="ls")          # relatif à la ligne de base, y vers le bas
    arr = np.array(masque, dtype="uint8").reshape(haut, larg)
    pad = np.zeros((haut + 2 * TAMPON, larg + 2 * TAMPON), dtype="uint8")
    pad[TAMPON:TAMPON + haut, TAMPON:TAMPON + larg] = arr
    octets = sdf(pad)
    return _glyphe_pb(code, octets.tobytes(), larg, haut, gauche, -haut_px - ASCENDANT, avance)


def fabriquer_plage(ttf, nom, debut, fin):
    """Le contenu d'un fichier `<début>-<fin>.pbf`."""
    font = ImageFont.truetype(ttf, TAILLE, layout_engine=ImageFont.Layout.BASIC)
    cmap = TTFont(ttf).getBestCmap()
    corps = b""
    for code in range(debut, fin + 1):
        if code in cmap:
            corps += _champ_octets(3, glyphe(font, code))
    pile = _champ_octets(1, nom.encode("utf-8")) + _champ_octets(2, ("%d-%d" % (debut, fin)).encode()) + corps
    return _champ_octets(1, pile)


def fabriquer_glyphes(dossier_polices, sortie):
    """Écrit glyphs/<police>/<début>-<fin>.pbf ; rend la liste des chemins écrits."""
    ecrits = []
    for nom, fichier in POLICES:
        d = os.path.join(sortie, "glyphs", nom)
        os.makedirs(d, exist_ok=True)
        for debut, fin in PLAGES:
            data = fabriquer_plage(os.path.join(dossier_polices, fichier), nom, debut, fin)
            p = os.path.join(d, "%d-%d.pbf" % (debut, fin))
            with open(p, "wb") as f:
                f.write(data)
            ecrits.append(p)
    return ecrits

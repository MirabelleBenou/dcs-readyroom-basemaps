"""Le sprite : grain du papier (clair et sombre), hachures des localités, arbres, barbules du rail."""
import io
import json
import os

import numpy as np
from PIL import Image
from scipy import ndimage

SUR = 4          # sur-échantillonnage du dessin, avant réduction

PAPIER_CLAIR = (0xf3, 0xeb, 0xd8)
PAPIER_SOMBRE = (0x24, 0x21, 0x1c)
ROUGE_BRIQUE = (0x9a, 0x4a, 0x32)
VERT = (0x5f, 0x73, 0x50)
ENCRE = (0x3b, 0x2f, 0x25)
BRIQUE_SOMBRE = (0xc0, 0x7a, 0x5e)
VERT_SOMBRE = (0x8a, 0x9e, 0x78)
ENCRE_SOMBRE = (0xd8, 0xcc, 0xb4)


def _grain(taille, base, graine, force=0.022, large=0.025):
    """Fibres (bruit fin) et taches (bruit large), périodiques : le motif se répète sans couture."""
    rng = np.random.default_rng(graine)
    fin = ndimage.gaussian_filter(rng.normal(0, 1, (taille, taille)), 0.7, mode="wrap")
    fin /= max(np.abs(fin).max(), 1e-9)
    gros = ndimage.gaussian_filter(rng.normal(0, 1, (taille, taille)), taille / 6.0, mode="wrap")
    gros /= max(np.abs(gros).max(), 1e-9)
    f = 1 + force * fin + large * gros
    rgb = np.clip(np.array(base, dtype="float64")[None, None, :] * f[..., None], 0, 255)
    img = np.zeros((taille, taille, 4), dtype="uint8")
    img[..., :3] = np.rint(rgb).astype("uint8")
    img[..., 3] = 255
    return Image.fromarray(img, "RGBA")


def _rgba_vide(w, h):
    return Image.new("RGBA", (w, h), (0, 0, 0, 0))


def _hachure(taille, couleur, alpha):
    """Hachures obliques ////, périodiques sur la tuile (trois passes décalées)."""
    n = taille * SUR
    img = Image.new("L", (n, n), 0)
    from PIL import ImageDraw
    d = ImageDraw.Draw(img)
    ep = max(1, int(round(1.2 * (taille / 4.0) * SUR)))
    for k in (-1, 0, 1):
        d.line([(-n // 2 + k * n, n + n // 2), (n + n // 2 + k * n, -n // 2)], fill=255, width=ep)
    img = img.resize((taille, taille), Image.LANCZOS)
    a = np.array(img, dtype="float64") / 255.0 * alpha
    out = np.zeros((taille, taille, 4), dtype="uint8")
    out[..., 0], out[..., 1], out[..., 2] = couleur
    out[..., 3] = np.rint(a * 255).astype("uint8")
    return Image.fromarray(out, "RGBA")


def _arbre(ratio, couleur):
    """Un arbre : un cercle ouvert et un trait de tronc (esquisse de référence : cercle vide, barre dessous)."""
    from PIL import ImageDraw
    w, h = 10 * ratio, 12 * ratio
    n_w, n_h = w * SUR, h * SUR
    img = Image.new("L", (n_w, n_h), 0)
    d = ImageDraw.Draw(img)
    r = 3.0 * ratio * SUR
    cx, cy = n_w / 2, 4.5 * ratio * SUR
    lw = max(1, int(round(0.9 * ratio * SUR)))
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=255, width=lw)
    d.line([(cx, cy + r), (cx, 11.0 * ratio * SUR)], fill=255, width=lw)
    img = img.resize((w, h), Image.LANCZOS)
    out = np.zeros((h, w, 4), dtype="uint8")
    out[..., 0], out[..., 1], out[..., 2] = couleur
    out[..., 3] = np.array(img)
    return Image.fromarray(out, "RGBA")


def _barbule(ratio, couleur):
    """Un trait en travers de la voie : le motif d'une ligne (largeur de ligne = hauteur du motif)."""
    from PIL import ImageDraw
    w, h = 24 * ratio, 12 * ratio
    n_w, n_h = w * SUR, h * SUR
    img = Image.new("L", (n_w, n_h), 0)
    d = ImageDraw.Draw(img)
    lw = max(1, int(round(0.9 * ratio * SUR)))
    d.line([(n_w / 2, 0), (n_w / 2, n_h)], fill=255, width=lw)
    img = img.resize((w, h), Image.LANCZOS)
    out = np.zeros((h, w, 4), dtype="uint8")
    out[..., 0], out[..., 1], out[..., 2] = couleur
    out[..., 3] = np.array(img)
    return Image.fromarray(out, "RGBA")


def _carre(ratio, couleur, trait):
    """Le carré des bourgs : un petit carré plein cerné d'encre (esquisse de référence : marqueur 's')."""
    from PIL import ImageDraw
    w = h = 8 * ratio
    n = w * SUR
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    m = 1.0 * ratio * SUR
    d.rectangle([m, m, n - m, n - m], fill=couleur + (255,), outline=trait + (255,), width=max(1, int(round(0.7 * ratio * SUR))))
    return img.resize((w, h), Image.LANCZOS)


def images(ratio):
    """Les images du sprite à un rapport de pixels donné (1 ou 2), dans un ordre fixe."""
    t = 256 * ratio
    h = 4 * ratio          # un trait oblique tous les 4 px, la densité de '////' de l'esquisse de référence à 100 dpi
    return [
        ("papier", _grain(t, PAPIER_CLAIR, 20261111)),
        ("papier-sombre", _grain(t, PAPIER_SOMBRE, 20261112, force=0.03, large=0.03)),
        ("hachure", _hachure(h, ROUGE_BRIQUE, 0.55)),
        ("hachure-sombre", _hachure(h, BRIQUE_SOMBRE, 0.6)),
        ("arbre", _arbre(ratio, VERT)),
        ("arbre-sombre", _arbre(ratio, VERT_SOMBRE)),
        ("barbule", _barbule(ratio, ENCRE)),
        ("barbule-sombre", _barbule(ratio, ENCRE_SOMBRE)),
        ("carre", _carre(ratio, ROUGE_BRIQUE, ENCRE)),
        ("carre-sombre", _carre(ratio, BRIQUE_SOMBRE, ENCRE_SOMBRE)),
    ]


def assembler(imgs, largeur_max=1024):
    """Rangement en étagères, ordre fixe : rend (image atlas, index json)."""
    x = y = rang = 0
    place = {}
    for nom, im in imgs:
        w, h = im.size
        if x + w > largeur_max:
            x, y = 0, y + rang
            rang = 0
        place[nom] = (x, y, w, h)
        x += w + 1
        rang = max(rang, h + 1)
    haut = y + rang
    atlas = _rgba_vide(largeur_max, haut)
    for nom, im in imgs:
        px, py, _, _ = place[nom]
        atlas.paste(im, (px, py))
    return atlas, place


def ecrire_sprite(sortie):
    d = os.path.join(sortie, "sprite")
    os.makedirs(d, exist_ok=True)
    for ratio, suffixe in ((1, ""), (2, "@2x")):
        atlas, place = assembler(images(ratio))
        buf = io.BytesIO()
        atlas.save(buf, format="PNG", optimize=False, compress_level=9)
        with open(os.path.join(d, "papier%s.png" % suffixe), "wb") as f:
            f.write(buf.getvalue())
        idx = {nom: {"x": x, "y": y, "width": w, "height": h, "pixelRatio": ratio}
               for nom, (x, y, w, h) in place.items()}
        with open(os.path.join(d, "papier%s.json" % suffixe), "w", encoding="utf-8") as f:
            json.dump(idx, f, indent=1, sort_keys=True)
            f.write("\n")

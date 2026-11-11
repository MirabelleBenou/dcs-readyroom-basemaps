"""Les styles MapLibre (spec v8) : le style P, clair, et sa dérive sombre. Mêmes tuiles, même sprite."""
import colorsys
import json
import re

# --------------------------------------------------------------------------- palettes
CLAIR = {
    "nom": "papier-clair",
    "papier": "#f3ebd8", "encre": "#3b2f25", "bleu": "#4d6f8c", "brique": "#9a4a32", "brun": "#8c6b4e",
    "vert": "#5f7350", "grille": "#5a6f80", "mer": "#dfe5df", "lacs": "#cdd9d6", "courbes": "#8a6a48",
    "frontieres": "#6b3d6b", "halo": "rgba(240,230,207,0.85)",
    "grain": "papier", "hachure": "hachure", "arbre": "arbre", "barbule": "barbule", "carre": "carre",
}
SOMBRE = {
    "nom": "papier-sombre",
    "papier": "#24211c", "encre": "#d8ccb4", "bleu": "#7f9db5", "brique": "#c07a5e", "brun": "#a88a6c",
    "vert": "#8a9e78", "grille": "#8aa0b0", "mer": "#1f2a2c", "lacs": "#28393b", "courbes": "#9a8366",
    "frontieres": "#a885a8", "halo": "rgba(36,33,28,0.85)",
    "grain": "papier-sombre", "hachure": "hachure-sombre", "arbre": "arbre-sombre", "barbule": "barbule-sombre",
    "carre": "carre-sombre",
}

PT = 100.0 / 72.0          # un point de matplotlib à 100 dpi, en pixels
SC_VUE, SC_DETAIL = 0.8, 2.2   # l'échelle des traits de papier.py : vue (~31 m/px, zoom 11), détail (~10 m/px, zoom 12,6)
Z_VUE, Z_DETAIL = 11.0, 12.6


def larg(pt, bas=0.6, haut=1.1):
    """Épaisseur en points matplotlib de papier.py -> interpolation par le zoom entre la vue et le détail."""
    v, d = round(pt * SC_VUE * PT, 3), round(pt * SC_DETAIL * PT, 3)
    return ["interpolate", ["linear"], ["zoom"], 5, round(v * bas, 3), Z_VUE, v, Z_DETAIL, d, 14, round(d * haut, 3)]


def taille(pt_vue, pt_detail, bas=0.65, haut=1.05):
    v, d = round(pt_vue * PT, 3), round(pt_detail * PT, 3)
    return ["interpolate", ["linear"], ["zoom"], 5, round(v * bas, 3), Z_VUE, v, Z_DETAIL, d, 14, round(d * haut, 3)]


def _source(nom, urlpm, **kw):
    s = {"url": urlpm}
    s.update(kw)
    return s


def construire(palette, attributions, centre=(0.0, 0.0), zoom=9.0):
    """Le style d'un thème. attributions : {"fond": "...", "relief": "..."} (versions courtes, d'après le manifeste)."""
    P = palette
    sombre = palette["nom"] == "papier-sombre"
    fond = "fond"
    L = []

    def ajouter(c):
        L.append(c)

    # 1 papier
    ajouter({"id": "papier", "type": "background", "paint": {"background-pattern": P["grain"]}})
    # 2 relief : l'encre de l'estompage, sur transparent
    rp = {"raster-fade-duration": 0}
    if sombre:
        rp.update({"raster-brightness-min": 1, "raster-brightness-max": 0, "raster-hue-rotate": 180,
                   "raster-opacity": 0.9})
    ajouter({"id": "relief", "type": "raster", "source": "relief", "paint": rp})
    # 3 mer et lavis
    ajouter({"id": "mer", "type": "fill", "source": fond, "source-layer": "mer",
             "paint": {"fill-color": P["mer"], "fill-antialias": False}})
    # 4 rivage : le trait de côte et les lignes à distance croissante
    ajouter({"id": "rivage-lignes", "type": "line", "source": fond, "source-layer": "rivage",
             "filter": [">", ["get", "rang"], 0],
             "layout": {"line-cap": "round", "line-join": "round"},
             "paint": {"line-color": P["bleu"], "line-width": larg(0.6),
                       "line-opacity": ["match", ["get", "rang"], 1, 0.55, 2, 0.4, 3, 0.28, 0.18]}})
    ajouter({"id": "rivage-cote", "type": "line", "source": fond, "source-layer": "rivage",
             "filter": ["==", ["get", "rang"], 0],
             "layout": {"line-cap": "round", "line-join": "round"},
             "paint": {"line-color": P["bleu"], "line-width": larg(1.3)}})
    # 5 courbes
    ajouter({"id": "courbes", "type": "line", "source": fond, "source-layer": "courbes",
             "filter": ["==", ["get", "maitresse"], 0],
             "layout": {"line-cap": "round", "line-join": "round"},
             "paint": {"line-color": P["courbes"], "line-width": larg(0.35), "line-opacity": 0.45}})
    ajouter({"id": "courbes-maitresses", "type": "line", "source": fond, "source-layer": "courbes",
             "filter": ["==", ["get", "maitresse"], 1],
             "layout": {"line-cap": "round", "line-join": "round"},
             "paint": {"line-color": P["courbes"], "line-width": larg(0.8), "line-opacity": 0.6}})
    # 6 forêts : des arbres, jamais d'aplat
    ajouter({"id": "forets", "type": "symbol", "source": fond, "source-layer": "forets",
             "layout": {"icon-image": P["arbre"], "icon-allow-overlap": True, "icon-ignore-placement": True,
                        "icon-size": ["interpolate", ["linear"], ["zoom"], 5, 0.45, Z_VUE, 0.62, Z_DETAIL, 1.37, 14, 1.45]}})
    # 7 eau et cours d'eau
    ajouter({"id": "eau", "type": "fill", "source": fond, "source-layer": "eau",
             "paint": {"fill-color": P["lacs"], "fill-antialias": False}})
    ajouter({"id": "eau-contour", "type": "line", "source": fond, "source-layer": "eau",
             "layout": {"line-cap": "round", "line-join": "round"},
             "paint": {"line-color": P["bleu"], "line-width": larg(0.8)}})
    ajouter({"id": "cours-eau-ruisseaux", "type": "line", "source": fond, "source-layer": "cours_eau",
             "filter": ["==", ["get", "classe"], "stream"],
             "layout": {"line-cap": "round", "line-join": "round"},
             "paint": {"line-color": P["bleu"], "line-width": larg(0.5), "line-opacity": 0.9}})
    ajouter({"id": "cours-eau-rivieres", "type": "line", "source": fond, "source-layer": "cours_eau",
             "filter": ["==", ["get", "classe"], "river"],
             "layout": {"line-cap": "round", "line-join": "round"},
             "paint": {"line-color": P["bleu"], "line-width": larg(1.0), "line-opacity": 0.9}})
    # 8 localités : hachures et contour
    ajouter({"id": "localites", "type": "fill", "source": fond, "source-layer": "localites",
             "paint": {"fill-pattern": P["hachure"], "fill-antialias": False}})
    ajouter({"id": "localites-contour", "type": "line", "source": fond, "source-layer": "localites",
             "layout": {"line-cap": "round", "line-join": "round"},
             "paint": {"line-color": P["brique"], "line-width": larg(0.7)}})
    # 9 routes : trois classes
    cap = {"line-cap": "round", "line-join": "round"}
    ajouter({"id": "routes-secondaires", "type": "line", "source": fond, "source-layer": "routes", "minzoom": 8,
             "filter": ["==", ["get", "classe"], "secondary"], "layout": cap,
             "paint": {"line-color": P["brun"], "line-width": larg(0.9), "line-opacity": 0.9}})
    ajouter({"id": "routes-principales", "type": "line", "source": fond, "source-layer": "routes",
             "filter": ["==", ["get", "classe"], "primary"], "layout": cap,
             "paint": {"line-color": P["brique"], "line-width": larg(1.6), "line-opacity": 0.9}})
    for classe, ext, inte in (("trunk", 3.4, 1.4), ("motorway", 3.8, 1.8)):
        ajouter({"id": "routes-%s-trait" % classe, "type": "line", "source": fond, "source-layer": "routes",
                 "filter": ["==", ["get", "classe"], classe], "layout": cap,
                 "paint": {"line-color": P["encre"], "line-width": larg(ext), "line-opacity": 0.8}})
    for classe, ext, inte in (("trunk", 3.4, 1.4), ("motorway", 3.8, 1.8)):
        ajouter({"id": "routes-%s-vide" % classe, "type": "line", "source": fond, "source-layer": "routes",
                 "filter": ["==", ["get", "classe"], classe], "layout": cap,
                 "paint": {"line-color": P["papier"], "line-width": larg(inte)}})
    # 10 rail : un trait d'encre et des barbules
    ajouter({"id": "rail", "type": "line", "source": fond, "source-layer": "rail", "minzoom": 7, "layout": cap,
             "paint": {"line-color": P["encre"], "line-width": larg(1.1)}})
    ajouter({"id": "rail-barbules", "type": "line", "source": fond, "source-layer": "rail", "minzoom": 9,
             "layout": {"line-join": "round"},
             "paint": {"line-pattern": P["barbule"], "line-width": 12}})
    # 11 pistes
    ajouter({"id": "pistes", "type": "line", "source": fond, "source-layer": "pistes", "minzoom": 8,
             "layout": {"line-cap": "butt", "line-join": "miter"},
             "paint": {"line-color": P["encre"], "line-width": larg(3.0)}})
    # 12 frontières
    ajouter({"id": "frontieres", "type": "line", "source": fond, "source-layer": "frontieres",
             "layout": {"line-cap": "butt", "line-join": "round"},
             "paint": {"line-color": P["frontieres"], "line-width": larg(1.3), "line-opacity": 0.7,
                       "line-dasharray": [7, 2, 1, 2]}})
    # 13 grille MGRS (droite) et ses étiquettes
    ajouter({"id": "grille", "type": "line", "source": fond, "source-layer": "grille",
             "filter": ["==", ["get", "type"], "ligne"],
             "paint": {"line-color": P["grille"], "line-width": larg(0.55), "line-opacity": 0.6}})
    mono = ["JetBrains Mono"]
    taille_mono = taille(6.4, 11.2, bas=0.85, haut=1.0)
    for axe, ancre in (("e", "top-left"), ("n", "bottom-left")):
        ajouter({"id": "grille-etiquettes-%s" % axe, "type": "symbol", "source": fond, "source-layer": "grille",
                 "filter": ["==", ["get", "type"], axe],
                 "layout": {"text-field": ["get", "texte"], "text-font": mono, "text-size": taille_mono,
                            "text-anchor": ancre, "text-offset": [0.25, 0.25 if axe == "e" else -0.25],
                            "text-allow-overlap": False, "text-padding": 2},
                 "paint": {"text-color": P["grille"], "text-halo-color": P["halo"], "text-halo-width": 1.2}})
    ajouter({"id": "grille-carres", "type": "symbol", "source": fond, "source-layer": "grille",
             "filter": ["==", ["get", "type"], "carre"],
             "layout": {"text-field": ["get", "texte"], "text-font": mono,
                        "text-size": ["interpolate", ["linear"], ["zoom"], 9, 13, 12, 18, 14, 22],
                        "text-letter-spacing": 0.15, "text-allow-overlap": False},
             "paint": {"text-color": P["grille"], "text-opacity": 0.55}})
    # 14 noms : villes en capitales, bourgs en bas de casse (Special Elite, halo papier) ; ni village ni son nom
    elite = ["Special Elite"]
    halo = {"text-color": P["encre"], "text-halo-color": P["halo"], "text-halo-width": 1.8}
    ajouter({"id": "noms-bourgs", "type": "symbol", "source": fond, "source-layer": "lieux", "minzoom": 8,
             "filter": ["==", ["get", "place"], "town"],
             "layout": {"icon-image": P["carre"], "icon-size": ["interpolate", ["linear"], ["zoom"], 8, 0.4, Z_VUE, 0.45, Z_DETAIL, 1.2],
                        "icon-allow-overlap": True,
                        "text-field": ["get", "nom"], "text-font": elite, "text-size": taille(10, 11.5),
                        "text-anchor": "bottom-left", "text-offset": [0.5, -0.2], "text-optional": True},
             "paint": halo})
    ajouter({"id": "noms-villes", "type": "symbol", "source": fond, "source-layer": "lieux",
             "filter": ["==", ["get", "place"], "city"],
             "layout": {"text-field": ["get", "nom"], "text-font": elite, "text-transform": "uppercase",
                        "text-size": taille(15, 16.5), "text-anchor": "bottom", "text-offset": [0, -0.4]},
             "paint": halo})
    return {
        "version": 8,
        "name": P["nom"],
        "center": [round(centre[0], 5), round(centre[1], 5)],
        "zoom": zoom,
        "sources": {
            "fond": _source("fond", "pmtiles://./fond.pmtiles", type="vector", attribution=attributions["fond"]),
            "relief": _source("relief", "pmtiles://./relief.pmtiles", type="raster", tileSize=512,
                              attribution=attributions["relief"]),
        },
        "sprite": "./sprite/papier",
        "glyphs": "./glyphs/{fontstack}/{range}.pbf",
        "layers": L,
    }


def ecrire_style(chemin, style):
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(style, f, indent=1, ensure_ascii=False)
        f.write("\n")


# --------------------------------------------------------------------------- le fond se tait
_HEX = re.compile(r"#[0-9a-fA-F]{6}\b")
_RGB = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)")


def saturation_hsl(r, g, b):
    h, l, s = colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)
    return s


def couleurs(style):
    """Toutes les couleurs écrites dans un style : [(texte, (r, g, b))]."""
    txt = json.dumps(style)
    out = []
    for m in _HEX.finditer(txt):
        h = m.group(0)[1:]
        out.append((m.group(0), tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))))
    for m in _RGB.finditer(txt):
        out.append((m.group(0), tuple(int(m.group(i)) for i in (1, 2, 3))))
    return out


def sature(style, maximum=0.60):
    """Les couleurs dont la saturation HSL dépasse le maximum."""
    return [(t, rgb) for t, rgb in couleurs(style) if saturation_hsl(*rgb) > maximum + 1e-9]

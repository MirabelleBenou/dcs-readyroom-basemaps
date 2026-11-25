"""La clé `exclusions` du descripteur : validation, lecture des relations, règles `autoroutes`, `frontieres_effacees` et
`frontieres_ajoutees`, refus d'une règle qui ne touche rien, journal. Fixtures OSM synthétiques, relations comprises."""
import json
import os
import re
import shutil

import numpy as np
import osmium
import pytest
from shapely.geometry import box

from conftest import LAT0, LAT1, LON0, LON1, RACINE, entrees_relief, lire_osm
from fabrique import fabriquer as fab, merc, osm, theatre as th
from fabrique.couches import ENTITES
from fabrique.magasin import Magasin
from fabrique.progres import Progres
from fabrique.theatre import TheatreErreur, charger_descripteur, valider_exclusions

COMMIT = "0123456789abcdef0123456789abcdef01234567"


# ---------------------------------------------------------------------- les fixtures
def ecrire_osm(chemin, chemins, relations=()):
    """Nœuds, chemins puis relations (l'ordre d'un extrait). `chemins` : [(id, [(lon, lat)...], tags)] ; chaque chemin a
    ses propres nœuds (aucun recollage). `relations` : [(id, tags, [id de chemin...])]."""
    nodes, ways, rels = [], [], []
    nid = [1000]
    for wid, pts, tags in chemins:
        refs = []
        for lon, lat in pts:
            nid[0] += 1
            nodes.append('<node id="%d" lat="%.6f" lon="%.6f"/>' % (nid[0], lat, lon))
            refs.append(nid[0])
        t = "".join('<tag k="%s" v="%s"/>' % (k, v) for k, v in tags.items())
        ways.append('<way id="%d">%s%s</way>' % (wid, "".join('<nd ref="%d"/>' % r for r in refs), t))
    for rid, tags, membres in relations:
        t = "".join('<tag k="%s" v="%s"/>' % (k, v) for k, v in {"type": "boundary", **tags}.items())
        rels.append('<relation id="%d">%s%s</relation>' % (
            rid, "".join('<member type="way" ref="%d" role="outer"/>' % m for m in membres), t))
    with open(chemin, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<osm version="0.6" generator="essai">\n')
        f.write("\n".join(nodes + ways + rels))
        f.write("\n</osm>\n")


def ligne(lat, lon0=35.97, lon1=36.03):
    """Un segment est-ouest ; décalé de 5 millièmes de degré vers le nord, pour ne pas toucher le bord de l'emprise d'essai."""
    return [(lon0, lat + 0.005), (lon1, lat + 0.005)]


AUTO = {"highway": "motorway"}


def chemins_de_base():
    c = [
        (1, ligne(35.400), {**AUTO, "ref": "A 17"}),
        (2, ligne(35.401), {**AUTO, "ref": "A17"}),
        (3, ligne(35.402), {**AUTO, "ref": "A 4;A 17"}),
        (4, ligne(35.403), {"highway": "motorway_link", "ref": "A 17"}),
        (5, ligne(35.404), {**AUTO, "ref": "A 18"}),
        (6, ligne(35.405), {"highway": "trunk", "ref": "A 17"}),
        (7, ligne(35.406), AUTO),
        (8, ligne(35.407), {**AUTO, "ref": "A 14"}),                          # le milieu est dans la zone Z
        (9, ligne(35.408, 35.97, 36.00), {**AUTO, "ref": "A 14"}),            # un bout dans la zone, pas le milieu
        (10, ligne(35.409, 35.97, 35.985), {**AUTO, "ref": "A 14"}),          # tout hors de la zone
        # les frontières d'État
        (20, ligne(35.410), {"boundary": "administrative", "admin_level": "2"}),      # RU, UA (et Hessen, Thüringen)
        (21, ligne(35.411), {"boundary": "administrative", "admin_level": "2"}),      # RU, TR
        (22, ligne(35.412), {"boundary": "administrative", "admin_level": "2"}),      # RU seul
        (23, ligne(35.413), {"boundary": "administrative", "admin_level": "2"}),      # aucune relation
        (24, ligne(35.414), {"boundary": "administrative", "admin_level": "2", "maritime": "yes"}),
        (25, ligne(35.415), {"boundary": "administrative", "admin_level": "2"}),      # RU, GE (par ISO3166-1)
        (26, ligne(35.416), {"boundary": "administrative", "admin_level": "2"}),      # RU, une relation sans code
        # les limites de Länder
        (30, ligne(35.420), {"boundary": "administrative", "admin_level": "4"}),      # Hessen, Thüringen : ajoutée
        (31, ligne(35.421), {"boundary": "administrative", "admin_level": "4"}),      # Hessen, Bayern : tous deux dans a
        (32, ligne(35.422), {"boundary": "administrative", "admin_level": "4"}),      # Thüringen seul
        (33, ligne(35.423, 35.97, 35.985), {"boundary": "administrative", "admin_level": "4"}),   # Hessen, Thüringen, hors Z
        (34, ligne(35.424), {"boundary": "administrative", "admin_level": "4", "maritime": "yes"}),   # maritime
        (35, ligne(35.425), {"boundary": "administrative", "admin_level": "4"}),      # Bayern, Thüringen (a et b)
    ]
    r = [
        (100, {"boundary": "administrative", "admin_level": "2", "ISO3166-1:alpha2": "RU", "name": "Russie"},
         [20, 21, 22, 24, 25, 26]),
        (101, {"boundary": "administrative", "admin_level": "2", "ISO3166-1:alpha2": "UA", "name": "Ukraine"}, [20, 24]),
        (102, {"boundary": "administrative", "admin_level": "2", "ISO3166-1:alpha2": "TR", "name": "Turquie"}, [21]),
        (103, {"boundary": "administrative", "admin_level": "2", "ISO3166-1": "GE", "name": "Géorgie"}, [25]),
        (104, {"boundary": "administrative", "admin_level": "2", "name": "Sans code"}, [26]),
        (200, {"boundary": "administrative", "admin_level": "4", "name": "Hessen"}, [20, 30, 31, 33, 34]),
        (201, {"boundary": "administrative", "admin_level": "4", "name": "Thüringen"}, [20, 30, 32, 33, 34, 35]),
        (202, {"boundary": "administrative", "admin_level": "4", "name": "Bayern"}, [31, 35]),
    ]
    return c, r


ZONE_Z = [35.99, 35.30, 36.01, 35.50]
REGLES_A_B = {"a": [["4", "Hessen"], ["4", "Bayern"]], "b": [["4", "Thüringen"]]}


def fabrique_extrait(tmp_path, nom="m.osm", chemins=None, relations=None):
    c, r = chemins_de_base()
    p = str(tmp_path / nom)
    ecrire_osm(p, c if chemins is None else chemins, r if relations is None else relations)
    return p


def lire(tmp_path, fichiers, exclusions, sous="t"):
    """Lit les extraits dans un magasin ; rend ({couche: [(ident, props, géométrie)]}, lignes du journal)."""
    x0, y0 = merc.lonlat_vers_merc(LON0 - 0.5, LAT0 - 0.5)
    x1, y1 = merc.lonlat_vers_merc(LON1 + 0.5, LAT1 + 0.5)
    travail = tmp_path / sous
    os.makedirs(str(travail), exist_ok=True)
    mg = Magasin(os.path.join(str(travail), "m.sqlite"))
    lignes = []
    try:
        osm.lire(fichiers if isinstance(fichiers, list) else [fichiers], mg, box(float(x0), float(y0), float(x1), float(y1)),
                 str(travail), journal=lignes.append, exclusions=exclusions)
        return {c: list(mg.entites(c)) for c in ENTITES}, lignes
    finally:
        mg.fermer()


def sans_lecture(lignes):
    """Le journal sans les lignes « OpenStreetMap lu : » (une par extrait lu), pour comparer le reste."""
    return [l for l in lignes if not l.startswith("OpenStreetMap lu : ")]


def idents(d, couche):
    return sorted(i for i, _, _ in d[couche])


def km_de(chemins, ids):
    """Les km au sol des chemins de la fixture dont l'identifiant est dans `ids`, une décimale."""
    total = 0.0
    for wid, pts, _ in chemins:
        if wid in ids:
            x, y = merc.lonlat_vers_merc([p[0] for p in pts], [p[1] for p in pts])
            total += osm.longueur_sol(np.column_stack([x, y]))
    return "%.1f" % (total / 1000.0)


# ---------------------------------------------------------------------- la clé absente
def test_cle_absente_lecture_identique_et_aucune_lecture_de_relations(tmp_path, monkeypatch):
    p = fabrique_extrait(tmp_path)
    appels = []
    vrai = osmium.FileProcessor

    def espion(chemin, entites=None, *a, **k):
        appels.append(entites)
        return vrai(chemin, entites, *a, **k) if entites is not None else vrai(chemin)
    monkeypatch.setattr(osmium, "FileProcessor", espion)
    d, lignes = lire(tmp_path, p, None)
    assert all(e != osmium.osm.RELATION for e in appels) and len(appels) == 1          # une seule lecture, la passe actuelle
    assert sans_lecture(lignes) == []                                                                # le journal ne change pas
    monkeypatch.undo()
    attendu, _ = lire_osm(p, tmp_path / "t2")
    assert {c: [(i, pr, g.wkb) for i, pr, g in v] for c, v in d.items()} == \
        {c: [(i, pr, g.wkb) for i, pr, g in v] for c, v in attendu.items()}
    # toutes les autoroutes, toutes les frontières d'État (hors maritime) sont là
    assert len(d["routes"]) == 10 and len(d["frontieres"]) == 6


def test_une_lecture_des_relations_par_fichier_quand_la_cle_est_la(tmp_path, monkeypatch):
    p = fabrique_extrait(tmp_path)
    q = fabrique_extrait(tmp_path, "n.osm")
    appels = []
    vrai = osmium.FileProcessor

    def espion(chemin, entites=None, *a, **k):
        appels.append(entites)
        return vrai(chemin, entites)
    monkeypatch.setattr(osmium, "FileProcessor", espion)
    lire(tmp_path, [p, q], {"autoroutes": [{"ref": "A 17"}]})
    assert appels[:2] == [osmium.osm.RELATION, osmium.osm.RELATION]              # les relations d'abord, seules
    assert len(appels) == 4 and all(e != osmium.osm.RELATION for e in appels[2:])


# ---------------------------------------------------------------------- le descripteur
ABSENTE = object()


def racine_avec(tmp_path, exclusions=ABSENTE):
    r = tmp_path / "racine"
    (r / "zones").mkdir(parents=True, exist_ok=True)
    (r / "theatres").mkdir(exist_ok=True)
    from conftest import emprise_geojson
    emprise_geojson(str(r / "zones" / "e.geojson"))
    d = {"nom": "mini", "emprise": "zones/e.geojson", "osm": ["https://exemple.invalid/mini.osm"]}
    if exclusions is not ABSENTE:
        d["exclusions"] = exclusions                  # json.dumps écrit NaN et Infinity, que json.load relit
    (r / "theatres" / "mini.json").write_text(json.dumps(d))
    return str(r)


TOUT_VALIDE = {
    "autoroutes": [{"ref": "A 17"}, {"ref": "A 14", "zone": [10.5, 51.56, 12.5, 54.2]}],
    "frontieres_effacees": [{"pays": ["RU", "UA"]}],
    "frontieres_ajoutees": [{"a": [["4", "Hessen"]], "b": [["4", "Thüringen"]], "zone": [1, 2, 3, 4]}, {"a": [["9", "X"]], "b": [["9", "Y"]]}],
}


def test_descripteur_avec_et_sans_exclusions(tmp_path):
    assert charger_descripteur("mini", racine_avec(tmp_path)).exclusions is None
    assert charger_descripteur("mini", racine_avec(tmp_path, TOUT_VALIDE)).exclusions == TOUT_VALIDE
    for partiel in ({"autoroutes": [{"ref": "A 1"}]}, {"frontieres_effacees": [{"pays": ["AA", "BB", "CC"]}]},
                    {"frontieres_ajoutees": [{"a": [["4", "x"]], "b": [["4", "y"]]}]}):
        assert charger_descripteur("mini", racine_avec(tmp_path, partiel)).exclusions == partiel


def avec(**plus):
    return dict(TOUT_VALIDE, **plus)


def regle(liste, **plus):
    base = {"autoroutes": {"ref": "A 1"}, "frontieres_effacees": {"pays": ["RU", "UA"]},
            "frontieres_ajoutees": {"a": [["4", "x"]], "b": [["4", "y"]]}}[liste]
    return {liste: [dict(base, **plus)]}


REFUS = [
    # (exclusions, morceau du message : la clé nommée)
    ([], "`exclusions` doit être un objet"),
    ("A 17", "`exclusions` doit être un objet"),
    (None, "`exclusions` doit être un objet"),
    ({}, "au moins une liste"),
    ({"autoroute": [{"ref": "A 1"}]}, "clé inconnue : autoroute"),
    ({"autoroutes": [{"ref": "A 1"}], "frontieres": []}, "clé inconnue : frontieres"),
    ({"autoroutes": []}, "exclusions.autoroutes"),
    ({"autoroutes": {"ref": "A 1"}}, "exclusions.autoroutes"),
    ({"frontieres_effacees": []}, "exclusions.frontieres_effacees"),
    ({"frontieres_ajoutees": []}, "exclusions.frontieres_ajoutees"),
    ({"autoroutes": ["A 1"]}, "exclusions.autoroutes[0]"),
    ({"autoroutes": [{}]}, "clé manquante : ref"),
    ({"autoroutes": [{"ref": "A 1", "tout": 1}]}, "clé inconnue : tout"),
    ({"autoroutes": [{"ref": "A 1", "pays": ["RU", "UA"]}]}, "clé inconnue : pays"),
    ({"autoroutes": [{"ref": ""}]}, "exclusions.autoroutes[0].ref"),
    ({"autoroutes": [{"ref": "  "}]}, "exclusions.autoroutes[0].ref"),
    ({"autoroutes": [{"ref": 17}]}, "exclusions.autoroutes[0].ref"),
    ({"autoroutes": [{"ref": ["A 17"]}]}, "exclusions.autoroutes[0].ref"),
    ({"autoroutes": [{"ref": "A 1"}, {"ref": "A 2", "zone": [1, 2, 3]}]}, "exclusions.autoroutes[1].zone"),
    ({"autoroutes": [{"ref": "A 1", "zone": [1, 2, 3, 4, 5]}]}, "exclusions.autoroutes[0].zone"),
    ({"autoroutes": [{"ref": "A 1", "zone": "1,2,3,4"}]}, "exclusions.autoroutes[0].zone"),
    ({"autoroutes": [{"ref": "A 1", "zone": [1, 2, "3", 4]}]}, "exclusions.autoroutes[0].zone"),
    ({"autoroutes": [{"ref": "A 1", "zone": [1, 2, True, 4]}]}, "exclusions.autoroutes[0].zone"),
    ({"autoroutes": [{"ref": "A 1", "zone": [1, 2, None, 4]}]}, "exclusions.autoroutes[0].zone"),
    ({"autoroutes": [{"ref": "A 1", "zone": [3, 2, 3, 4]}]}, "lon_min < lon_max"),
    ({"autoroutes": [{"ref": "A 1", "zone": [4, 2, 3, 4]}]}, "lon_min < lon_max"),
    ({"autoroutes": [{"ref": "A 1", "zone": [1, 4, 3, 4]}]}, "lat_min < lat_max"),
    ({"autoroutes": [{"ref": "A 1", "zone": [1, 5, 3, 4]}]}, "lat_min < lat_max"),
    ({"autoroutes": [{"ref": "A 1", "zone": [1, 2, float("inf"), 4]}]}, "exclusions.autoroutes[0].zone"),
    ({"autoroutes": [{"ref": "A 1", "zone": [1, 2, float("nan"), 4]}]}, "exclusions.autoroutes[0].zone"),
    ({"frontieres_effacees": [{}]}, "clé manquante : pays"),
    ({"frontieres_effacees": [{"pays": ["RU", "UA"], "zone": [1, 2, 3, 4]}]}, "clé inconnue : zone"),
    ({"frontieres_effacees": [{"pays": ["RU"]}]}, "exclusions.frontieres_effacees[0].pays"),
    ({"frontieres_effacees": [{"pays": []}]}, "exclusions.frontieres_effacees[0].pays"),
    ({"frontieres_effacees": [{"pays": "RU,UA"}]}, "exclusions.frontieres_effacees[0].pays"),
    ({"frontieres_effacees": [{"pays": ["RU", "ua"]}]}, "exclusions.frontieres_effacees[0].pays"),
    ({"frontieres_effacees": [{"pays": ["RU", "UKR"]}]}, "exclusions.frontieres_effacees[0].pays"),
    ({"frontieres_effacees": [{"pays": ["RU", "U1"]}]}, "exclusions.frontieres_effacees[0].pays"),
    ({"frontieres_effacees": [{"pays": ["RU", 4]}]}, "exclusions.frontieres_effacees[0].pays"),
    ({"frontieres_ajoutees": [{"a": [["4", "x"]]}]}, "clé manquante : b"),
    ({"frontieres_ajoutees": [{"b": [["4", "x"]]}]}, "clé manquante : a"),
    ({"frontieres_ajoutees": [{"a": [["4", "x"]], "b": [["4", "y"]], "pays": ["RU", "UA"]}]}, "clé inconnue : pays"),
    ({"frontieres_ajoutees": [{"a": [], "b": [["4", "y"]]}]}, "exclusions.frontieres_ajoutees[0].a"),
    ({"frontieres_ajoutees": [{"a": [["4", "x"]], "b": []}]}, "exclusions.frontieres_ajoutees[0].b"),
    ({"frontieres_ajoutees": [{"a": "Hessen", "b": [["4", "y"]]}]}, "exclusions.frontieres_ajoutees[0].a"),
    ({"frontieres_ajoutees": [{"a": [["4", "x"]], "b": [["4"]]}]}, "exclusions.frontieres_ajoutees[0].b[0]"),
    ({"frontieres_ajoutees": [{"a": [["4", "x", "z"]], "b": [["4", "y"]]}]}, "exclusions.frontieres_ajoutees[0].a[0]"),
    ({"frontieres_ajoutees": [{"a": [["4", "x"], "Hessen"], "b": [["4", "y"]]}]}, "exclusions.frontieres_ajoutees[0].a[1]"),
    ({"frontieres_ajoutees": [{"a": [[4, "x"]], "b": [["4", "y"]]}]}, "exclusions.frontieres_ajoutees[0].a[0]"),
    ({"frontieres_ajoutees": [{"a": [["4", ""]], "b": [["4", "y"]]}]}, "exclusions.frontieres_ajoutees[0].a[0]"),
    ({"frontieres_ajoutees": [{"a": [["", "x"]], "b": [["4", "y"]]}]}, "exclusions.frontieres_ajoutees[0].a[0]"),
    ({"frontieres_ajoutees": [{"a": [["4", "x"]], "b": [["4", "y"]], "zone": [1, 2, 3, 2]}]}, "lat_min < lat_max"),
]


@pytest.mark.parametrize("exclusions, message", REFUS)
def test_exclusions_mal_formees_refusees_au_chargement(tmp_path, exclusions, message):
    r = racine_avec(tmp_path, exclusions)
    with pytest.raises(TheatreErreur) as e:
        charger_descripteur("mini", r)
    assert message in str(e.value)


def test_cle_inconnue_a_tout_niveau_nomme_la_cle():
    for x, nom in (({"nouveau": []}, "nouveau"), ({"autoroutes": [{"ref": "A 1", "nouveau": 1}]}, "nouveau"),
                   ({"frontieres_effacees": [{"pays": ["RU", "UA"], "nouveau": 1}]}, "nouveau"),
                   ({"frontieres_ajoutees": [{"a": [["4", "x"]], "b": [["4", "y"]], "nouveau": 1}]}, "nouveau")):
        with pytest.raises(TheatreErreur, match=nom):
            valider_exclusions(x)


# ---------------------------------------------------------------------- autoroutes
def test_autoroutes_ref_avec_et_sans_espace_compose_et_lien(tmp_path):
    p = fabrique_extrait(tmp_path)
    avant, _ = lire(tmp_path, p, {"frontieres_effacees": [{"pays": ["RU", "UA"]}]}, "t0")
    d, _ = lire(tmp_path, p, {"autoroutes": [{"ref": "A 17"}], "frontieres_effacees": [{"pays": ["RU", "UA"]}]}, "t1")
    assert len(avant["routes"]) == 10
    # retirés : « A 17 », « A17 », « A 4;A 17 » et la bretelle « A 17 » ; restent « A 18 », la route nationale « A 17 »
    # (trunk), l'autoroute sans ref et les trois « A 14 »
    assert idents(d, "routes") == ["c10", "c5", "c6", "c7", "c8", "c9"]
    assert sorted(i for i, _, _ in avant["routes"]) == sorted(["c%d" % n for n in range(1, 11)])
    # la règle écrite sans espace retire la même chose
    d2, _ = lire(tmp_path, p, {"autoroutes": [{"ref": "A17"}], "frontieres_effacees": [{"pays": ["RU", "UA"]}]}, "t2")
    assert idents(d2, "routes") == idents(d, "routes")
    # un ref composé se retire par l'un de ses éléments seulement : « A 4 » ne retire que le chemin 3
    d3, _ = lire(tmp_path, p, {"autoroutes": [{"ref": "A 4"}], "frontieres_effacees": [{"pays": ["RU", "UA"]}]}, "t3")
    assert [n for n in range(1, 11) if "c%d" % n not in idents(d3, "routes")] == [3]
    # un élément n'est pas un morceau : « A 1 » ne retire ni « A 17 » ni « A 14 »
    with pytest.raises(TheatreErreur, match=r"autoroutes\[0\]"):
        lire(tmp_path, p, {"autoroutes": [{"ref": "A 1"}]}, "t4")


def test_autoroutes_la_zone_juge_le_milieu_de_la_longueur(tmp_path):
    p = fabrique_extrait(tmp_path)
    d, _ = lire(tmp_path, p, {"autoroutes": [{"ref": "A 14", "zone": ZONE_Z}], "frontieres_effacees": [{"pays": ["RU", "UA"]}]})
    # le chemin 8 (milieu dedans, bouts dehors) part ; le 9 (un bout dedans, milieu dehors) et le 10 (tout dehors) restent
    assert idents(d, "routes") == sorted(["c%d" % n for n in range(1, 11) if n != 8])


def test_autoroutes_bords_de_la_zone_compris(tmp_path):
    from shapely.geometry import LineString
    chemins = [(1, ligne(35.40), {**AUTO, "ref": "A 1"})]
    p = fabrique_extrait(tmp_path, chemins=chemins, relations=[])
    x, y = merc.lonlat_vers_merc([c[0] for c in chemins[0][1]], [c[1] for c in chemins[0][1]])
    milieu = LineString(np.column_stack([x, y])).interpolate(0.5, normalized=True)
    lon, lat = (float(v) for v in merc.merc_vers_lonlat(milieu.x, milieu.y))
    # le milieu est sur le bord ouest, puis sur le bord sud, puis sur le bord est, puis sur le bord nord : compris
    for k, zone in enumerate(([lon, 35.0, lon + 1, 36.0], [35.0, lat, 37.0, lat + 1], [35.0, 35.0, lon, 36.0],
                              [35.0, 35.0, 37.0, lat])):
        lire(tmp_path, p, {"autoroutes": [{"ref": "A 1", "zone": zone}]}, "b%d" % k)
    with pytest.raises(TheatreErreur):
        lire(tmp_path, p, {"autoroutes": [{"ref": "A 1", "zone": [lon + 0.001, 35.0, lon + 1, 36.0]}]}, "b9")


# ---------------------------------------------------------------------- frontieres_effacees
def test_effacement_un_non_effacement_et_le_chemin_a_un_seul_pays_code(tmp_path):
    p = fabrique_extrait(tmp_path)
    d, _ = lire(tmp_path, p, {"frontieres_effacees": [{"pays": ["RU", "UA"]}]})
    # 20 (RU, UA) part ; 21 (RU, TR : TR hors du groupe), 25 (RU, GE : GE hors du groupe), 22 (RU seul), 23 (aucune relation),
    # 26 (RU et une relation sans code) restent ; 24 est maritime : jamais lu
    assert idents(d, "frontieres") == ["c21", "c22", "c23", "c25", "c26"]
    # le groupe est large : 20 et 25 partent, 21 reste (TR n'y est pas)
    d, _ = lire(tmp_path, p, {"frontieres_effacees": [{"pays": ["RU", "UA", "GE"]}]}, "t2")
    assert idents(d, "frontieres") == ["c21", "c22", "c23", "c26"]
    # le code vient de ISO3166-1 à défaut de ISO3166-1:alpha2 (le chemin 25) ; l'ordre des pays n'importe pas
    d, _ = lire(tmp_path, p, {"frontieres_effacees": [{"pays": ["GE", "RU"]}]}, "t3")
    assert idents(d, "frontieres") == ["c20", "c21", "c22", "c23", "c26"]
    # deux règles : chacune efface ce qu'elle touche
    d, _ = lire(tmp_path, p, {"frontieres_effacees": [{"pays": ["RU", "UA"]}, {"pays": ["RU", "TR"]}]}, "t4")
    assert idents(d, "frontieres") == ["c22", "c23", "c25", "c26"]


def test_les_autres_limites_ne_sont_pas_des_frontieres_sans_regle_d_ajout(tmp_path):
    p = fabrique_extrait(tmp_path)
    d, _ = lire(tmp_path, p, {"frontieres_effacees": [{"pays": ["RU", "UA"]}]})
    assert not any(i in idents(d, "frontieres") for i in ("c30", "c31", "c32", "c33", "c35"))


# ---------------------------------------------------------------------- frontieres_ajoutees
def test_ajout_entre_a_et_b_pas_entre_deux_relations_de_a(tmp_path):
    p = fabrique_extrait(tmp_path)
    base = {"frontieres_effacees": [{"pays": ["RU", "UA"]}]}
    d, _ = lire(tmp_path, p, dict(base, frontieres_ajoutees=[REGLES_A_B]))
    f = idents(d, "frontieres")
    # 30 (Hessen, Thüringen), 33 (idem, sans zone), 35 (Bayern, Thüringen) sont ajoutés ; 31 (Hessen, Bayern : a et a) non ;
    # 32 (Thüringen seul) non ; 34 est maritime
    assert [i for i in f if i in ("c30", "c31", "c32", "c33", "c34", "c35")] == ["c30", "c33", "c35"]
    # un chemin tracé est un chemin d'État comme les autres : mêmes propriétés
    props = {i: pr for i, pr, _ in d["frontieres"]}
    assert props["c30"] == props["c21"] == {}


def test_ajout_la_zone_juge_le_milieu(tmp_path):
    p = fabrique_extrait(tmp_path)
    base = {"frontieres_effacees": [{"pays": ["RU", "UA"]}]}
    d, _ = lire(tmp_path, p, dict(base, frontieres_ajoutees=[dict(REGLES_A_B, zone=ZONE_Z)]))
    f = idents(d, "frontieres")
    # le chemin 33 a son milieu (35.9775 °E) hors de la zone ; 30 et 35 l'ont dedans
    assert [i for i in f if i in ("c30", "c31", "c32", "c33", "c35")] == ["c30", "c35"]


def test_ajout_deux_listes_de_paires_nommees(tmp_path):
    p = fabrique_extrait(tmp_path)
    base = {"frontieres_effacees": [{"pays": ["RU", "UA"]}]}
    # `b` à plusieurs paires, `a` à une : Hessen contre Bayern ou Thüringen
    d, _ = lire(tmp_path, p, dict(base, frontieres_ajoutees=[{"a": [["4", "Hessen"]], "b": [["4", "Bayern"], ["4", "Thüringen"]]}]))
    assert [i for i in idents(d, "frontieres") if i in ("c30", "c31", "c32", "c33", "c35")] == ["c30", "c31", "c33"]
    # le niveau compte : « Hessen » au niveau 5 n'est pas la relation lue
    with pytest.raises(TheatreErreur, match="Hessen"):
        lire(tmp_path, p, dict(base, frontieres_ajoutees=[{"a": [["5", "Hessen"]], "b": [["4", "Thüringen"]]}]), "t2")


def test_un_chemin_efface_ne_revient_pas_par_un_ajout(tmp_path):
    """Le chemin 20 est membre de Hessen et de Thüringen : la règle d'ajout le tracerait ; la règle d'effacement (RU, UA)
    l'a effacé, il ne revient pas. Sans la règle d'effacement, il se trace (une seule fois)."""
    p = fabrique_extrait(tmp_path)
    d, _ = lire(tmp_path, p, {"frontieres_effacees": [{"pays": ["RU", "UA"]}], "frontieres_ajoutees": [REGLES_A_B]})
    assert "c20" not in idents(d, "frontieres")
    d, _ = lire(tmp_path, p, {"frontieres_effacees": [{"pays": ["RU", "TR"]}], "frontieres_ajoutees": [REGLES_A_B]}, "t2")
    f = [i for i, _, _ in d["frontieres"]]
    assert f.count("c20") == 1 and len(f) == len(set(f))


def test_un_chemin_deja_trace_n_est_pas_trace_deux_fois_ni_compte_comme_ajoute(tmp_path):
    """Le chemin 20 (admin_level 2, non effacé ici) est aussi entre Hessen et Thüringen : tracé une fois, et la règle
    d'ajout, qui ne trace que les 30, 33 et 35, le compte pas."""
    p = fabrique_extrait(tmp_path)
    d, lignes = lire(tmp_path, p, {"frontieres_effacees": [{"pays": ["RU", "TR"]}], "frontieres_ajoutees": [REGLES_A_B]})
    assert [i for i, _, _ in d["frontieres"]].count("c20") == 1
    ajout = [l for l in lignes if l.startswith("exclusion : frontieres_ajoutees")][0]
    assert ": 3 chemins, " in ajout


# ---------------------------------------------------------------------- les relations seules disent la frontière
RELATIONS_AB = [(200, {"boundary": "administrative", "admin_level": "4", "name": "Hessen"}, [40, 41, 42]),
                (201, {"boundary": "administrative", "admin_level": "4", "name": "Thüringen"}, [40, 41, 43])]
REGLE_AB = {"frontieres_ajoutees": [{"a": [["4", "Hessen"]], "b": [["4", "Thüringen"]]}]}


def test_un_chemin_sans_etiquette_membre_de_a_et_de_b_est_trace(tmp_path):
    chemins = [(40, ligne(35.40), {}),                                  # aucune étiquette, membre de a et de b
               (41, ligne(35.41), {"maritime": "yes"}),                 # idem, maritime
               (42, ligne(35.42), {}),                                  # membre de a seulement
               (43, ligne(35.43), {})]                                  # membre de b seulement
    p = fabrique_extrait(tmp_path, chemins=chemins, relations=RELATIONS_AB)
    d, lignes = lire(tmp_path, p, REGLE_AB)
    assert idents(d, "frontieres") == ["c40"]
    assert any(l.startswith("exclusion : frontieres_ajoutees[0]") and ": 1 chemins, " in l for l in sans_lecture(lignes))


def test_une_regle_sans_chemin_commun_aux_relations_echoue_avant_la_premiere_passe(tmp_path, monkeypatch):
    chemins = [(42, ligne(35.42), {}), (43, ligne(35.43), {})]
    relations = [(200, {"boundary": "administrative", "admin_level": "4", "name": "Hessen"}, [42]),
                 (201, {"boundary": "administrative", "admin_level": "4", "name": "Thüringen"}, [43])]
    p = fabrique_extrait(tmp_path, chemins=chemins, relations=relations)
    appels = []
    vrai = osmium.FileProcessor

    def espion(chemin, entites=None, *a, **k):
        appels.append(entites)
        return vrai(chemin, entites, *a, **k) if entites is not None else vrai(chemin)
    monkeypatch.setattr(osmium, "FileProcessor", espion)
    x0, y0 = merc.lonlat_vers_merc(LON0 - 0.5, LAT0 - 0.5)
    x1, y1 = merc.lonlat_vers_merc(LON1 + 0.5, LAT1 + 0.5)
    mg = Magasin(str(tmp_path / "m.sqlite"))
    lignes = []
    with pytest.raises(TheatreErreur) as e:
        osm.charger([p], mg, box(float(x0), float(y0), float(x1), float(y1)), str(tmp_path), journal=lignes.append,
                    exclusions=REGLE_AB)
    mg.fermer()
    nom = osm._nom_regle("frontieres_ajoutees", 0, REGLE_AB["frontieres_ajoutees"][0])
    assert str(e.value) == "exclusions : règle %s : aucun chemin commun aux relations de `a` et de `b`" % nom
    assert appels == [osmium.osm.RELATION]                       # la lecture des relations seule
    assert not any(l.startswith("exclusion :") for l in lignes) and lignes == []


def test_une_ligne_de_journal_par_extrait_lu(tmp_path):
    p = fabrique_extrait(tmp_path)
    q = fabrique_extrait(tmp_path, "n.osm")
    _, lignes = lire(tmp_path, [p, q], {"autoroutes": [{"ref": "A 17"}]})
    lus = [l for l in lignes if l.startswith("OpenStreetMap lu : ")]
    assert len(lus) == 2
    assert re.fullmatch(r"OpenStreetMap lu : m\.osm \(1/2\) en \d+ s", lus[0])
    assert re.fullmatch(r"OpenStreetMap lu : n\.osm \(2/2\) en \d+ s", lus[1])
    _, lignes = lire(tmp_path, p, None, "t2")                    # sans exclusions aussi
    assert len(lignes) == 1 and re.fullmatch(r"OpenStreetMap lu : m\.osm \(1/1\) en \d+ s", lignes[0])


# ---------------------------------------------------------------------- la règle 6
def test_une_regle_qui_ne_touche_rien_fait_echouer_et_se_nomme(tmp_path):
    p = fabrique_extrait(tmp_path)
    ok = {"frontieres_effacees": [{"pays": ["RU", "UA"]}]}
    cas = [
        (dict(ok, autoroutes=[{"ref": "A 17"}, {"ref": "A 99"}]), r'autoroutes\[1\] \{"ref": "A 99"\}'),
        (dict(ok, autoroutes=[{"ref": "A 14", "zone": [1.0, 2.0, 3.0, 4.0]}]), r'autoroutes\[0\] .*"zone": \[1.0, 2.0, 3.0, 4.0\]'),
        (dict(ok, frontieres_effacees=[{"pays": ["RU", "UA"]}, {"pays": ["FR", "DE"]}]), r'frontieres_effacees\[1\] \{"pays": \["FR", "DE"\]\}'),
        (dict(ok, frontieres_effacees=[{"pays": ["RU", "ZZ"]}]), r"frontieres_effacees\[0\]"),
        (dict(ok, frontieres_ajoutees=[{"a": [["4", "Bayern"]], "b": [["4", "Hessen"]], "zone": [1.0, 2.0, 3.0, 4.0]}]),
         r"frontieres_ajoutees\[0\]"),
        (dict(ok, frontieres_ajoutees=[{"a": [["4", "Hessen"], ["4", "Bayern"]], "b": [["4", "Hessen"]]}]), None),
        (dict(ok, frontieres_ajoutees=[{"a": [["4", "Bayern"]], "b": [["4", "Absente"]]}]), r"frontieres_ajoutees\[0\].*Absente"),
        (dict(ok, frontieres_ajoutees=[{"a": [["4", "Absente"]], "b": [["4", "Hessen"]]}]), r"frontieres_ajoutees\[0\].*Absente"),
    ]
    for k, (x, motif) in enumerate(cas):
        if motif is None:                    # une règle qui touche (les chemins 31, 33, 34... de Hessen et Bayern) : pas d'échec
            lire(tmp_path, p, x, "ok%d" % k)
            continue
        with pytest.raises(TheatreErreur, match=motif):
            lire(tmp_path, p, x, "t%d" % k)


def test_la_paire_absente_echoue_avant_la_passe_actuelle(tmp_path, monkeypatch):
    p = fabrique_extrait(tmp_path)
    appels = []
    vrai = osmium.FileProcessor

    def espion(chemin, entites=None, *a, **k):
        appels.append(entites)
        return vrai(chemin, entites)
    monkeypatch.setattr(osmium, "FileProcessor", espion)
    with pytest.raises(TheatreErreur, match="Absente"):
        lire(tmp_path, p, {"frontieres_ajoutees": [{"a": [["4", "Absente"]], "b": [["4", "Hessen"]]}]})
    assert appels == [osmium.osm.RELATION]


def test_une_regle_vide_se_dit_au_journal_avant_d_echouer(tmp_path, monkeypatch):
    p = fabrique_extrait(tmp_path)
    x0, y0 = merc.lonlat_vers_merc(LON0 - 0.5, LAT0 - 0.5)
    x1, y1 = merc.lonlat_vers_merc(LON1 + 0.5, LAT1 + 0.5)
    mg = Magasin(str(tmp_path / "m.sqlite"))
    lignes = []
    with pytest.raises(TheatreErreur) as e:
        osm.charger([p], mg, box(float(x0), float(y0), float(x1), float(y1)), str(tmp_path), journal=lignes.append,
                    exclusions={"autoroutes": [{"ref": "A 17"}, {"ref": "A 99"}, {"ref": "A 98"}]})
    mg.fermer()
    assert any(l.startswith('exclusion : autoroutes[1] {"ref": "A 99"} : 0 chemins, 0.0 km') for l in lignes)
    assert 'autoroutes[1] {"ref": "A 99"}' in str(e.value) and 'autoroutes[2] {"ref": "A 98"}' in str(e.value)
    assert 'autoroutes[0]' not in str(e.value)


# ---------------------------------------------------------------------- le journal
def test_les_lignes_du_journal(tmp_path):
    chemins, rel = chemins_de_base()
    p = fabrique_extrait(tmp_path)
    x = {"autoroutes": [{"ref": "A 17"}, {"ref": "A 14", "zone": ZONE_Z}],
         "frontieres_effacees": [{"pays": ["RU", "UA", "GE"]}],
         "frontieres_ajoutees": [dict(REGLES_A_B, zone=ZONE_Z)]}
    d, lignes = lire(tmp_path, p, x)
    assert sans_lecture(lignes) == [
        'exclusion : autoroutes[0] {"ref": "A 17"} : 4 chemins, %s km' % km_de(chemins, {1, 2, 3, 4}),
        'exclusion : autoroutes[1] {"ref": "A 14", "zone": [35.99, 35.3, 36.01, 35.5]} : 1 chemins, %s km' % km_de(chemins, {8}),
        'exclusion : frontieres_effacees[0] {"pays": ["RU", "UA", "GE"]} : 2 chemins, %s km ; GE-RU %s km ; RU-UA %s km'
        % (km_de(chemins, {20, 25}), km_de(chemins, {25}), km_de(chemins, {20})),
        'exclusion : frontieres_ajoutees[0] {"a": [["4", "Hessen"], ["4", "Bayern"]], "b": [["4", "Thüringen"]], '
        '"zone": [35.99, 35.3, 36.01, 35.5]} : 2 chemins, %s km' % km_de(chemins, {30, 35}),
        # les frontières d'État tracées, par paire : 21 (RU-TR) ; 22, 23, 26 (moins de deux relations codées)
        'frontières tracées par paire : ? %s km, RU-TR %s km' % (km_de(chemins, {22, 23, 26}), km_de(chemins, {21})),
    ]


def test_journal_d_une_frontiere_sans_aucune_trace(tmp_path):
    chemins = [(1, ligne(35.40), {**AUTO, "ref": "A 1"})]
    p = fabrique_extrait(tmp_path, chemins=chemins, relations=[])
    d, lignes = lire(tmp_path, p, {"autoroutes": [{"ref": "A 1"}]})
    assert sans_lecture(lignes) == ['exclusion : autoroutes[0] {"ref": "A 1"} : 1 chemins, %s km' % km_de(chemins, {1}),
                      "frontières tracées par paire : aucune"]
    assert d["routes"] == []


def test_une_relation_dans_deux_extraits_compte_une_fois(tmp_path):
    chemins, _ = chemins_de_base()
    p = fabrique_extrait(tmp_path)
    q = fabrique_extrait(tmp_path, "n.osm")                  # le même extrait : mêmes chemins, mêmes relations
    x = {"frontieres_effacees": [{"pays": ["RU", "UA"]}], "frontieres_ajoutees": [REGLES_A_B]}
    seul, l1 = lire(tmp_path, p, x, "t1")
    deux, l2 = lire(tmp_path, [p, q], x, "t2")
    assert sans_lecture(l1) == sans_lecture(l2)
    assert {c: [i for i, _, _ in v] for c, v in seul.items()} == {c: [i for i, _, _ in v] for c, v in deux.items()}
    # une relation en double dans un seul fichier ne double pas les codes d'un chemin (elle compte une fois)
    c, r = chemins_de_base()
    p3 = fabrique_extrait(tmp_path, "d.osm", relations=r + [r[0]])
    d, _ = lire(tmp_path, p3, {"frontieres_effacees": [{"pays": ["RU", "TR"]}]}, "t3")
    assert "c21" not in idents(d, "frontieres") and "c22" in idents(d, "frontieres")


# ---------------------------------------------------------------------- les descripteurs du dépôt
GERMANYCW = {
    "autoroutes": [
        {"ref": "A 17"}, {"ref": "A 20"}, {"ref": "A 38"}, {"ref": "A 71"}, {"ref": "A 143"},
        {"ref": "A 14", "zone": [10.5, 51.56, 12.5, 54.2]},
        {"ref": "A 44", "zone": [9.56, 50.8, 10.3, 51.4]},
        {"ref": "A 72", "zone": [12.2, 50.88, 12.9, 51.4]},
        {"ref": "A 73", "zone": [10.5, 50.17, 11.2, 50.75]},
        {"ref": "A 26", "zone": [9.3, 53.4, 10.05, 53.7]},
        {"ref": "A 31", "zone": [7.05, 52.22, 7.45, 52.62]},
        {"ref": "A 33", "zone": [8.25, 51.99, 8.52, 52.12]},
        {"ref": "A 30", "zone": [8.72, 52.19, 8.86, 52.25]},
        {"ref": "A 6", "zone": [11.95, 49.4, 12.56, 49.7]},
        {"ref": "A2", "zone": [14.6, 51.9, 17.6, 52.6]},
        {"ref": "S3", "zone": [14.5, 51.1, 15.9, 53.12]},
        {"ref": "S5", "zone": [16.3, 51.1, 17.6, 53.2]},
        {"ref": "S6", "zone": [14.9, 53.5, 17.6, 54.6]},
        {"ref": "S11", "zone": [16.6, 51.9, 17.4, 52.6]},
        {"ref": "D5", "zone": [12.4, 49.55, 13.95, 49.95]},
        {"ref": "D8", "zone": [13.85, 50.25, 14.45, 50.9]},
        {"ref": "D6", "zone": [12.15, 49.95, 13.0, 50.3]},
    ],
    "frontieres_effacees": [{"pays": ["CZ", "SK"]}],
    "frontieres_ajoutees": [
        {"a": [["4", "Schleswig-Holstein"], ["4", "Niedersachsen"], ["4", "Hessen"], ["4", "Bayern"]],
         "b": [["4", "Mecklenburg-Vorpommern"], ["4", "Brandenburg"], ["4", "Sachsen-Anhalt"], ["4", "Thüringen"],
               ["4", "Sachsen"]]},
        {"a": [["9", "Charlottenburg-Wilmersdorf"], ["9", "Spandau"], ["9", "Steglitz-Zehlendorf"],
               ["9", "Tempelhof-Schöneberg"], ["9", "Neukölln"], ["9", "Reinickendorf"], ["10", "Moabit"],
               ["10", "Hansaviertel"], ["10", "Tiergarten"], ["10", "Wedding"], ["10", "Gesundbrunnen"],
               ["10", "Kreuzberg"]],
         "b": [["4", "Brandenburg"], ["9", "Pankow"], ["9", "Lichtenberg"], ["9", "Marzahn-Hellersdorf"],
               ["9", "Treptow-Köpenick"], ["10", "Mitte"], ["10", "Friedrichshain"]],
         "zone": [12.9, 52.3, 13.9, 52.7]},
    ],
}
CAUCASUS = {"frontieres_effacees": [{"pays": ["RU", "UA", "GE", "AM", "AZ"]}]}


def test_les_descripteurs_du_depot_portent_les_exclusions_attendues():
    assert charger_descripteur("germanycw").exclusions == GERMANYCW
    assert charger_descripteur("caucasus").exclusions == CAUCASUS
    for nom, attendu in (("germanycw", GERMANYCW), ("caucasus", CAUCASUS)):
        with open(os.path.join(RACINE, "theatres", nom + ".json"), encoding="utf-8") as f:
            assert json.load(f)["exclusions"] == attendu
    for nom in ("syria", "essai-lattaquie"):
        with open(os.path.join(RACINE, "theatres", nom + ".json"), encoding="utf-8") as f:
            assert "exclusions" not in json.load(f)
        assert charger_descripteur(nom).exclusions is None


# ---------------------------------------------------------------------- `theatre`, de bout en bout
def lancer(tmp_path, exclusions, nom_dalles):
    """Un théâtre minuscule, fabriqué de bout en bout depuis un extrait en cache : rend (journal, dossiers)."""
    r = racine_avec(tmp_path, exclusions)
    for d in ("travail", "cache", "dossier"):
        (tmp_path / d).mkdir(exist_ok=True)
    (tmp_path / "cache" / "osm").mkdir(exist_ok=True)
    url = "https://exemple.invalid/mini.osm"
    shutil.copy(str(tmp_path / "x.osm"), th.chemin_osm(str(tmp_path / "cache"), url))
    lignes = []
    jeu = {"dossier": tmp_path}
    th.theatre("mini", str(tmp_path / "dossier" / "sortie"), str(tmp_path / "travail"), str(tmp_path / "cache"), racine=r,
               environ={"FABRIQUE_COMMIT": COMMIT}, journal=lignes.append,
               progres=Progres(ecrire=lambda l: None, intervalle=60), entrees_relief=entrees_relief(jeu, nom_dalles))
    return lignes


def test_theatre_de_bout_en_bout_journal_et_refus(tmp_path):
    chemins, rel = chemins_de_base()
    ecrire_osm(str(tmp_path / "x.osm"), chemins, rel)
    lignes = lancer(tmp_path, {"autoroutes": [{"ref": "A 17"}], "frontieres_effacees": [{"pays": ["RU", "UA"]}]}, "a")
    assert any(l.startswith('exclusion : autoroutes[0] {"ref": "A 17"} : 4 chemins, ') for l in lignes)
    assert any(l.startswith("exclusion : frontieres_effacees[0] ") for l in lignes)
    assert any(l.startswith("frontières tracées par paire : ") for l in lignes)
    assert os.path.isdir(str(tmp_path / "dossier" / "sortie")) and os.listdir(str(tmp_path / "travail")) == []
    shutil.rmtree(str(tmp_path / "dossier" / "sortie"))
    shutil.rmtree(str(tmp_path / "racine"))
    # une règle qui ne touche rien : la fabrication échoue, rien n'est posé, le dossier de travail est vide
    with pytest.raises(TheatreErreur, match=r"autoroutes\[0\] \{\"ref\": \"A 99\"\}"):
        lancer(tmp_path, {"autoroutes": [{"ref": "A 99"}]}, "b")
    assert not os.path.exists(str(tmp_path / "dossier" / "sortie")) and os.listdir(str(tmp_path / "travail")) == []

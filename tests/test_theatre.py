"""La commande `theatre` : descripteur, refus d'avant écriture, cache, `.part`, droits, travail vide, progression."""
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error

import osmium
import pytest

from conftest import RACINE, emprise_geojson, entrees_relief
from fabrique import cli, fabriquer as fab, theatre as th
from fabrique.progres import Progres
from fabrique.theatre import TheatreErreur, charger_descripteur
from fabrique.verifier import controles

URL = "https://exemple.invalid/mini.osm"
COMMIT = "0123456789abcdef0123456789abcdef01234567"


def racine_factice(tmp_path, descripteurs=None, zone="e.geojson"):
    """Un faux dépôt : `zones/<zone>` et des descripteurs {nom de fichier: contenu (dict ou texte)}."""
    r = tmp_path / "racine"
    (r / "zones").mkdir(parents=True)
    (r / "theatres").mkdir()
    emprise_geojson(str(r / "zones" / zone))
    for nom, d in (descripteurs or {}).items():
        (r / "theatres" / (nom + ".json")).write_text(d if isinstance(d, str) else json.dumps(d))
    return str(r)


def valide(nom="mini", **plus):
    d = {"nom": nom, "emprise": "zones/e.geojson", "osm": [URL]}
    d.update(plus)
    return d


def photo(*dossiers):
    out = []
    for d in dossiers:
        for r, ds, fs in os.walk(d):
            out += [os.path.join(r, n) for n in ds + fs]
    return sorted(out)


# ---------------------------------------------------------------------- le descripteur
def test_tous_les_descripteurs_du_depot_sont_valides():
    noms = th.lister_descripteurs()
    assert "essai-lattaquie" in noms
    for nom in noms:
        d = charger_descripteur(nom)
        assert d.nom == nom and os.path.isfile(d.emprise) and d.osm
        assert d.em_wgs.bounds[1] > 0 and d.em_wgs.bounds[3] > 0                 # hémisphère nord
        assert all(u.startswith("https://") for u in d.osm)


def test_le_theatre_d_essai():
    with open(os.path.join(RACINE, "theatres", "essai-lattaquie.json"), encoding="utf-8") as f:
        assert json.load(f) == {"nom": "essai-lattaquie", "emprise": "zones/lattaquie.geojson",
                                "osm": ["https://download.geofabrik.de/asia/syria-latest.osm.pbf"]}
    d = charger_descripteur("essai-lattaquie")
    assert d.bloc_zoom == 8
    from fabrique.emprise import surface_km2
    assert surface_km2(d.em_wgs) < 3000


def test_descripteur_valide_et_bloc_zoom(tmp_path):
    r = racine_factice(tmp_path, {"mini": valide(), "dix": valide("dix", bloc_zoom=10), "a-b-9": valide("a-b-9")})
    assert charger_descripteur("mini", r).bloc_zoom == 8
    assert charger_descripteur("dix", r).bloc_zoom == 10
    assert charger_descripteur("a-b-9", r).nom == "a-b-9"


@pytest.mark.parametrize("contenu, message", [
    (dict(valide(), inconnue=1), "clé inconnue"),
    ({"nom": "mini", "emprise": "zones/e.geojson"}, "clé manquante"),
    ({"emprise": "zones/e.geojson", "osm": [URL]}, "clé manquante"),
    (valide(osm=["http://exemple.invalid/mini.osm"]), "HTTPS seulement"),
    (valide(osm=["ftp://exemple.invalid/mini.osm"]), "HTTPS seulement"),
    (valide(osm=["//exemple.invalid/mini.osm"]), "HTTPS seulement"),
    (valide(osm=["HTTPS://exemple.invalid/mini.osm"]), "HTTPS seulement"),
    (valide(osm=[]), "liste non vide"),
    (valide(osm="https://exemple.invalid/mini.osm"), "liste non vide"),
    (valide(osm=["https://exemple.invalid/mini.zip"]), "attendu"),
    (valide(osm=["https://exemple.invalid/mini.osm?x=1"]), "paramètres"),
    (valide(osm=["https://moi:secret@exemple.invalid/mini.osm"]), "identifiants"),
    (valide(osm=["https://exemple.invalid/"]), "attendu"),
    (valide(emprise="/etc/passwd"), "relatif"),
    (valide(emprise="../e.geojson"), "sous zones/"),
    (valide(emprise="zones/../e.geojson"), "sous zones/"),
    (valide(emprise="autre/e.geojson"), "sous zones/"),
    (valide(emprise="zones/absente.geojson"), "introuvable"),
    (valide(emprise=3), "relatif"),
    (valide(bloc_zoom=6), "bloc_zoom"),
    (valide(bloc_zoom=11), "bloc_zoom"),
    (valide(bloc_zoom=True), "bloc_zoom"),
    (valide(bloc_zoom="8"), "bloc_zoom"),
    (valide(nom="autre"), "n'égale pas"),
    ([1, 2], "objet JSON"),
    ("{pas du json", "illisible"),
])
def test_descripteurs_refuses(tmp_path, contenu, message):
    r = racine_factice(tmp_path, {"mini": contenu})
    with pytest.raises(TheatreErreur, match=message):
        charger_descripteur("mini", r)


@pytest.mark.parametrize("nom", ["Mini", "-mini", "mini_x", "a" * 33, "", "../mini", "mini/x", "mini.json", "mini x"])
def test_noms_invalides(tmp_path, nom):
    r = racine_factice(tmp_path, {"mini": valide()})
    with pytest.raises(TheatreErreur, match="nom de théâtre invalide"):
        charger_descripteur(nom, r)


def test_nom_a_32_caracteres_accepte_et_theatre_inconnu(tmp_path):
    n = "a" * 32
    r = racine_factice(tmp_path, {n: valide(n)})
    assert charger_descripteur(n, r).nom == n
    with pytest.raises(TheatreErreur, match="inconnu"):
        charger_descripteur("absent", r)


def test_emprise_au_sud_de_l_equateur_refusee(tmp_path):
    r = racine_factice(tmp_path, {"mini": valide(emprise="zones/sud.geojson")})
    emprise_geojson(os.path.join(r, "zones", "sud.geojson"), (35.0, 35.4, -1.0, 1.0))
    with pytest.raises(TheatreErreur, match="hémisphère nord"):
        charger_descripteur("mini", r)


# ---------------------------------------------------------------------- les refus d'avant écriture
@pytest.fixture
def espace(tmp_path):
    for d in ("travail", "cache", "dossier"):
        (tmp_path / d).mkdir()
    r = racine_factice(tmp_path, {"mini": valide()})
    return {"racine": r, "sortie": str(tmp_path / "dossier" / "sortie"), "travail": str(tmp_path / "travail"),
            "cache": str(tmp_path / "cache"), "tmp": tmp_path}


@pytest.fixture
def sans_fabrication(monkeypatch):
    """Si un refus d'avant écriture laissait passer, la fabrication elle-même se lancerait : elle échoue ici."""
    def interdit(*a, **k):
        raise AssertionError("la fabrication ne devait pas commencer")
    monkeypatch.setattr(fab, "_fabriquer", interdit)
    monkeypatch.setattr(th, "obtenir_osm", interdit)


def appeler(e, nom="mini", environ=None, **plus):
    return th.theatre(nom, e["sortie"], e["travail"], e["cache"], racine=e["racine"], environ=environ or {},
                      progres=Progres(ecrire=lambda l: None, intervalle=60), journal=lambda *a: None, **plus)


def refuse(e, message, **kw):
    avant = photo(e["tmp"])
    with pytest.raises(TheatreErreur, match=message):
        appeler(e, **kw)
    assert photo(e["tmp"]) == avant, "rien ne s'écrit avant un refus"


def test_refus_avant_ecriture(espace, sans_fabrication):
    e = espace
    refuse(e, "inconnu", nom="absent")
    refuse(e, "FABRIQUE_COMMIT", environ={"FABRIQUE_COMMIT": "A" * 40})                 # majuscules
    refuse(e, "FABRIQUE_COMMIT", environ={"FABRIQUE_COMMIT": "a" * 39})
    refuse(e, "FABRIQUE_COMMIT", environ={"FABRIQUE_COMMIT": ""})
    refuse(e, "FABRIQUE_COMMIT", environ={"FABRIQUE_COMMIT": "g" * 40})
    # --sortie existe (dossier, fichier, lien cassé)
    os.mkdir(e["sortie"])
    refuse(e, "--sortie existe")
    os.rmdir(e["sortie"])
    open(e["sortie"], "w").close()
    refuse(e, "--sortie existe")
    os.remove(e["sortie"])
    os.symlink(str(e["tmp"] / "nulle-part"), e["sortie"])
    refuse(e, "--sortie existe")
    os.remove(e["sortie"])
    # le parent de --sortie
    e2 = dict(e, sortie=str(e["tmp"] / "pas-la" / "sortie"))
    refuse(e2, "parent de --sortie")
    # --travail : absent, fichier, non vide
    refuse(dict(e, travail=str(e["tmp"] / "pas-de-travail")), "--travail n'existe pas")
    (e["tmp"] / "fichier").write_text("x")
    refuse(dict(e, travail=str(e["tmp"] / "fichier")), "--travail n'existe pas")
    (e["tmp"] / "travail" / "reste").write_text("x")
    refuse(e, "--travail n'est pas vide")
    os.remove(e["tmp"] / "travail" / "reste")
    # --cache : absent, fichier
    refuse(dict(e, cache=str(e["tmp"] / "pas-de-cache")), "--cache n'existe pas")
    refuse(dict(e, cache=str(e["tmp"] / "fichier")), "--cache n'existe pas")


def test_refus_du_descripteur_avant_ecriture(espace, sans_fabrication):
    e = espace
    (e["tmp"] / "racine" / "theatres" / "mauvais.json").write_text(json.dumps(valide("mauvais", osm=["http://x/a.osm"])))
    refuse(e, "HTTPS", nom="mauvais")


def test_refus_si_travail_et_sortie_sont_sur_deux_systemes_de_fichiers(espace, sans_fabrication, monkeypatch):
    real = os.stat

    class Faux:
        def __init__(self, s):
            self.s = s

        def __getattr__(self, k):
            return 12345 if k == "st_dev" else getattr(self.s, k)

    monkeypatch.setattr(os, "stat", lambda p, *a, **k: Faux(real(p, *a, **k)) if str(p) == espace["travail"] else real(p, *a, **k))
    refuse(espace, "même système de fichiers")


def test_cli_refuse_avec_un_code_non_nul_et_un_message(espace, capsys):
    e = espace
    code = cli.principal(["theatre", "absent", "--sortie", e["sortie"], "--travail", e["travail"], "--cache", e["cache"]])
    assert code == 2
    assert "erreur : " in capsys.readouterr().err
    assert not os.path.exists(e["sortie"])


def test_theatre_n_importe_jamais_overpass_et_le_cli_refuse_en_sous_processus(espace):
    e = espace
    r = subprocess.run([sys.executable, "-c",
                        "import sys; from fabrique import cli, theatre; assert 'fabrique.overpass' not in sys.modules; "
                        "code = cli.principal(['theatre', 'absent', '--sortie', %r, '--travail', %r, '--cache', %r]); "
                        "assert 'fabrique.overpass' not in sys.modules; sys.exit(code)" % (e["sortie"], e["travail"], e["cache"])],
                       capture_output=True, text=True, cwd=RACINE, env=dict(os.environ, PYTHONPATH=RACINE))
    assert r.returncode == 2, r.stderr
    assert "erreur : " in r.stderr


# ---------------------------------------------------------------------- les extraits : le cache et le téléchargement
class Reponse:
    """Une réponse HTTP minimale, pour `ouvrir` : `contenu` (octets), `status`, `longueur` (en-tête), `casse` (lève en route)."""

    def __init__(self, contenu, status=200, longueur="auto", casse=False):
        self.flux = io.BytesIO(contenu)
        self.status = status
        self.casse = casse
        self.headers = {} if longueur is None else {"Content-Length": str(len(contenu) if longueur == "auto" else longueur)}

    def read(self, n=-1):
        d = self.flux.read(n if n and n > 0 else 1024)
        if self.casse and self.flux.tell() > 0 and d:
            raise ConnectionResetError("coupée en route")
        return d

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def un_osm(tmp_path, nom="mini.osm"):
    from conftest import osm_xml
    chemin = str(tmp_path / nom)
    osm_xml(chemin)
    return chemin


def un_pbf(tmp_path):
    """Un minuscule .osm.pbf, écrit par pyosmium."""
    chemin = str(tmp_path / "mini.osm.pbf")
    w = osmium.SimpleWriter(chemin)
    w.add_node(osmium.osm.mutable.Node(id=1, location=(36.0, 35.4), tags={"place": "town", "name": "X"}))
    w.close()
    return chemin


def test_chemin_du_cache_osm(tmp_path):
    url = "https://exemple.invalid/dossier/syria-latest.osm.pbf"
    attendu = hashlib.sha256(url.encode()).hexdigest()[:16] + "-syria-latest.osm.pbf"
    assert th.chemin_osm("/c", url) == "/c/osm/" + attendu


def test_extrait_present_dans_le_cache_sans_toucher_le_reseau(tmp_path):
    cache = tmp_path / "cache"
    (cache / "osm").mkdir(parents=True)
    f = th.chemin_osm(str(cache), URL)
    shutil.copy(un_osm(tmp_path), f)

    def reseau(*a, **k):
        raise AssertionError("le réseau ne se touche pas")
    assert th.obtenir_osm(URL, str(cache), ouvrir=reseau) == f


@pytest.mark.parametrize("nom", ["mini.osm", "mini.osm.gz", "mini.osm.pbf"])
def test_telechargement_reussi_dans_le_cache(tmp_path, nom):
    import gzip
    brut = {"mini.osm": lambda: open(un_osm(tmp_path), "rb").read(),
            "mini.osm.gz": lambda: gzip.compress(open(un_osm(tmp_path), "rb").read()),
            "mini.osm.pbf": lambda: open(un_pbf(tmp_path), "rb").read()}[nom]()
    cache = tmp_path / "cache"
    cache.mkdir()
    vus = []

    def ouvrir(req, timeout=None):
        vus.append((req.full_url, req.get_header("User-agent")))
        return Reponse(brut)
    url = "https://exemple.invalid/" + nom
    f = th.obtenir_osm(url, str(cache), ouvrir=ouvrir)
    assert open(f, "rb").read() == brut and f == th.chemin_osm(str(cache), url)
    assert vus == [(url, "dcs-readyroom-basemaps (+https://github.com/MirabelleBenou/dcs-readyroom-basemaps)")]
    assert [n for n in os.listdir(cache / "osm") if n.endswith(".part")] == []
    assert th.obtenir_osm(url, str(cache), ouvrir=lambda *a, **k: 1 / 0) == f          # le second passage : le cache


def une_erreur_efface_le_part(tmp_path, ouvrir, message):
    cache = tmp_path / "cache"
    cache.mkdir(exist_ok=True)
    with pytest.raises(TheatreErreur, match=message):
        th.obtenir_osm(URL, str(cache), ouvrir=ouvrir)
    assert os.listdir(cache / "osm") == [], "ni `.part` ni fichier à moitié"


def test_part_efface_sur_une_coupure(tmp_path):
    une_erreur_efface_le_part(tmp_path, lambda req, timeout=None: Reponse(b"<osm>" * 5000, casse=True), "téléchargement impossible")


def test_part_efface_sur_une_erreur_du_serveur_ou_du_reseau(tmp_path):
    def e404(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

    def hs(req, timeout=None):
        raise urllib.error.URLError("pas de réseau")
    une_erreur_efface_le_part(tmp_path, e404, "téléchargement impossible")
    une_erreur_efface_le_part(tmp_path, hs, "téléchargement impossible")


def test_part_efface_sur_un_code_autre_que_200(tmp_path):
    une_erreur_efface_le_part(tmp_path, lambda req, timeout=None: Reponse(b"x", status=206), "HTTP 206")


def test_part_efface_sur_une_taille_differente_de_content_length(tmp_path):
    brut = open(un_osm(tmp_path), "rb").read()
    une_erreur_efface_le_part(tmp_path, lambda req, timeout=None: Reponse(brut, longueur=len(brut) + 10), "tronquée")


def test_part_efface_sur_un_en_tete_illisible(tmp_path):
    une_erreur_efface_le_part(tmp_path, lambda req, timeout=None: Reponse(b"ceci n'est pas de l'OSM" * 50), "en-tête illisible")


def test_sans_content_length_la_taille_n_est_pas_exigee(tmp_path):
    brut = open(un_osm(tmp_path), "rb").read()
    cache = tmp_path / "cache"
    cache.mkdir()
    f = th.obtenir_osm(URL, str(cache), ouvrir=lambda req, timeout=None: Reponse(brut, longueur=None))
    assert os.path.getsize(f) == len(brut)


def test_une_redirection_hors_https_est_refusee():
    h = th._RedirectionHttps()
    import urllib.request
    req = urllib.request.Request("https://exemple.invalid/a.osm")
    with pytest.raises(urllib.error.URLError):
        h.redirect_request(req, None, 302, "Found", {}, "http://exemple.invalid/b.osm")
    assert h.redirect_request(req, None, 302, "Found", {}, "https://exemple.invalid/b.osm") is not None


# ---------------------------------------------------------------------- une fabrication complète sur le jeu minuscule
class Horloge:
    def __init__(self):
        self.t = []

    def ecrire(self, ligne):
        self.t.append((time.monotonic(), ligne))


@pytest.fixture(scope="module")
def reussite(jeu, tmp_path_factory):
    """`theatre` sur le jeu minuscule, sous `umask 077`, le cache rempli d'avance, Git interdit, le réseau interdit."""
    import fabrique.manifeste as mf
    tmp = tmp_path_factory.mktemp("theatre")
    r = racine_factice(tmp, {"mini": valide()})
    for d in ("sortie_parent", "travail", "cache"):
        (tmp / d).mkdir()
    (tmp / "cache" / "osm").mkdir()
    shutil.copy(jeu["osm"], th.chemin_osm(str(tmp / "cache"), URL))
    horloge = Horloge()

    def git(*a, **k):
        raise AssertionError("Git ne s'appelle jamais quand FABRIQUE_COMMIT est posée")
    sortie = str(tmp / "sortie_parent" / "sortie")
    ancien = os.umask(0o077)
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(mf.subprocess, "run", git)
            res = th.theatre("mini", sortie, str(tmp / "travail"), str(tmp / "cache"), racine=r,
                             environ={"FABRIQUE_COMMIT": COMMIT}, journal=lambda *a: None,
                             progres=Progres(ecrire=horloge.ecrire, intervalle=0.4), ouvrir=lambda *a, **k: 1 / 0,
                             entrees_relief=entrees_relief(jeu, "t"))
    finally:
        os.umask(ancien)
    return {"sortie": sortie, "tmp": tmp, "res": res, "horloge": horloge, "cache": str(tmp / "cache"),
            "travail": str(tmp / "travail")}


def test_theatre_rend_un_dossier_complet_et_verifie(reussite):
    ok, res = controles(reussite["sortie"], journal=lambda *a: None)
    assert ok, res["echecs"]
    assert len(os.listdir(reussite["sortie"])) == 8
    assert os.listdir(reussite["travail"]) == [], "--travail est vide à la sortie"
    assert os.path.isdir(reussite["travail"])


def test_droits_sous_umask_077(reussite):
    d = reussite["sortie"]
    for r, ds, fs in os.walk(d):
        assert oct(os.stat(r).st_mode & 0o777) == "0o755", r
        for f in fs:
            assert oct(os.stat(os.path.join(r, f)).st_mode & 0o777) == "0o644", f
            assert not os.path.islink(os.path.join(r, f))


def test_manifeste_de_theatre(reussite, jeu):
    m = json.load(open(os.path.join(reussite["sortie"], "manifeste.json")))
    assert m["fabrique"] == {"commit": COMMIT, "modifie": False}
    assert m["entrees"]["theatre"] == "mini"
    assert m["execution"]["theatre"] is True
    (o,) = m["entrees"]["osm"]
    assert set(o) == {"adresse", "fichier", "taille", "sha256"}
    assert o["adresse"] == URL and o["fichier"] == "mini.osm"
    assert o["taille"] == os.path.getsize(jeu["osm"])
    assert o["sha256"] == hashlib.sha256(open(jeu["osm"], "rb").read()).hexdigest()
    assert set(m["entrees"]["emprise"]) == {"fichier", "taille", "sha256", "geometrie"}      # sa forme actuelle
    texte = open(os.path.join(reussite["sortie"], "manifeste.json")).read()
    assert str(reussite["tmp"]) not in texte, "jamais un chemin du cache ni du travail au manifeste"
    for e in m["entrees"]["relief"]:
        assert "fichier" not in e


def test_theatre_donne_les_memes_fichiers_que_fabriquer(reussite, jeu, tmp_path):
    out = str(tmp_path / "f")
    fab.fabriquer(str(reussite["tmp"] / "racine" / "zones" / "e.geojson"), [jeu["osm"]], str(tmp_path / "c"), out, journal=lambda *a: None, entrees_relief=entrees_relief(jeu, "f"))
    for f in sorted(os.listdir(out)):
        if f == "manifeste.json":
            continue
        a, b = os.path.join(out, f), os.path.join(reussite["sortie"], f)
        if os.path.isdir(a):
            assert photo(a) == [p.replace(reussite["sortie"], out) for p in photo(b)]
            for pa in photo(a):
                if os.path.isfile(pa):
                    assert open(pa, "rb").read() == open(pa.replace(out, reussite["sortie"]), "rb").read(), pa
        else:
            assert open(a, "rb").read() == open(b, "rb").read(), f
    ma = json.load(open(os.path.join(out, "manifeste.json")))
    mb = json.load(open(os.path.join(reussite["sortie"], "manifeste.json")))
    for k in ("dependances", "bibliotheques", "reglages", "noms", "python"):
        assert ma[k] == mb[k], k
    assert ma["entrees"]["relief"] == mb["entrees"]["relief"] and ma["entrees"]["polices"] == mb["entrees"]["polices"]


def test_progression_et_battement(reussite):
    lignes = [l for _, l in reussite["horloge"].t]
    assert lignes and all(l.startswith("progression : ") for l in lignes)
    assert any("blocs du zoom 8" in l and "/" in l for l in lignes)                      # un compte à chaque bloc
    assert any("vérification" in l for l in lignes)
    ecarts = [b - a for (a, _), (b, _) in zip(reussite["horloge"].t, reussite["horloge"].t[1:])]
    assert max(ecarts) < 4.0, max(ecarts)


def test_le_battement_redit_l_etape_et_le_temps_avec_un_intervalle_injecte():
    lignes = []
    with Progres(ecrire=lignes.append, intervalle=0.05) as p:
        p.etape("une longue étape", 3, 10)
        time.sleep(0.5)
    battements = [l for l in lignes if "en cours depuis" in l]
    assert len(battements) >= 4
    assert all(l.startswith("progression : une longue étape 3/10") and " s au total" in l for l in battements)


def test_pas_de_battement_apres_la_fin_et_une_ligne_par_etape():
    lignes = []
    with Progres(ecrire=lignes.append, intervalle=0.05) as p:
        p.etape("a")
        p.avancer(1, 2, "b")
        p.avancer(2, 2)
    n = len(lignes)
    time.sleep(0.2)
    assert len(lignes) == n
    assert lignes[0] == "progression : a" and "progression : b 1/2" in lignes and "progression : b 2/2" in lignes


def test_aucun_secret_ni_adresse_dans_la_progression(reussite):
    for _, l in reussite["horloge"].t:
        assert "://" not in l and "@" not in l


# ---------------------------------------------------------------------- les erreurs : `--travail` vide, pas de sortie
def test_travail_vide_apres_une_erreur_injectee_au_milieu(jeu, tmp_path, monkeypatch):
    r = racine_factice(tmp_path, {"mini": valide()})
    for d in ("travail", "cache", "dossier"):
        (tmp_path / d).mkdir()
    (tmp_path / "cache" / "osm").mkdir()
    shutil.copy(jeu["osm"], th.chemin_osm(str(tmp_path / "cache"), URL))
    from fabrique import terrain

    def casse(grille, entrees, em, dossier, journal=print, progres=None):
        open(os.path.join(dossier, "reste.bin"), "wb").write(b"x" * 1000)           # du travail en cours
        os.makedirs(os.path.join(dossier, "sous", "dossier"))
        raise RuntimeError("erreur injectée")
    monkeypatch.setattr(terrain, "construire", casse)
    sortie = str(tmp_path / "dossier" / "sortie")
    with pytest.raises(RuntimeError, match="injectée"):
        th.theatre("mini", sortie, str(tmp_path / "travail"), str(tmp_path / "cache"), racine=r, environ={},
                   journal=lambda *a: None, progres=Progres(ecrire=lambda l: None, intervalle=60),
                   entrees_relief=entrees_relief(jeu, "e"))
    assert os.listdir(tmp_path / "travail") == [] and os.path.isdir(tmp_path / "travail")
    assert not os.path.lexists(sortie)
    assert os.listdir(tmp_path / "dossier") == []


def test_travail_vide_apres_un_echec_du_telechargement(tmp_path):
    r = racine_factice(tmp_path, {"mini": valide()})
    for d in ("travail", "cache", "dossier"):
        (tmp_path / d).mkdir()
    sortie = str(tmp_path / "dossier" / "sortie")
    with pytest.raises(TheatreErreur, match="téléchargement impossible"):
        th.theatre("mini", sortie, str(tmp_path / "travail"), str(tmp_path / "cache"), racine=r, environ={},
                   journal=lambda *a: None, progres=Progres(ecrire=lambda l: None, intervalle=60),
                   ouvrir=lambda req, timeout=None: Reponse(b"x" * 3000, casse=True))
    assert os.listdir(tmp_path / "travail") == [] and not os.path.lexists(sortie)
    assert [n for n in os.listdir(tmp_path / "cache" / "osm")] == []


def test_la_verification_finale_refuse_un_dossier_qui_ne_passe_pas(jeu, tmp_path, monkeypatch):
    """Code non nul et sortie absente si les contrôles échouent."""
    r = racine_factice(tmp_path, {"mini": valide()})
    for d in ("travail", "cache", "dossier"):
        (tmp_path / d).mkdir()
    (tmp_path / "cache" / "osm").mkdir()
    shutil.copy(jeu["osm"], th.chemin_osm(str(tmp_path / "cache"), URL))
    from fabrique import verifier
    monkeypatch.setattr(th.ver, "controles", lambda d, j=print: (False, {"echecs": ["panne simulée"]}))
    sortie = str(tmp_path / "dossier" / "sortie")
    with pytest.raises(TheatreErreur, match="panne simulée"):
        th.theatre("mini", sortie, str(tmp_path / "travail"), str(tmp_path / "cache"), racine=r, environ={},
                   journal=lambda *a: None, progres=Progres(ecrire=lambda l: None, intervalle=60),
                   entrees_relief=entrees_relief(jeu, "v"))
    assert not os.path.lexists(sortie) and os.listdir(tmp_path / "travail") == []
    assert verifier.controles is not None


def test_tout_ce_qui_est_temporaire_passe_sous_travail(jeu, tmp_path, monkeypatch):
    import tempfile
    r = racine_factice(tmp_path, {"mini": valide()})
    for d in ("travail", "cache", "dossier"):
        (tmp_path / d).mkdir()
    (tmp_path / "cache" / "osm").mkdir()
    shutil.copy(jeu["osm"], th.chemin_osm(str(tmp_path / "cache"), URL))
    vus = []
    vrai = fab._fabriquer

    def espion(*a, **k):
        vus.append((tempfile.gettempdir(), os.environ["TMPDIR"], os.environ["SQLITE_TMPDIR"], os.environ["CPL_TMPDIR"]))
        return vrai(*a, **k)
    monkeypatch.setattr(fab, "_fabriquer", espion)
    avant = tempfile.gettempdir(), os.environ.get("TMPDIR")
    th.theatre("mini", str(tmp_path / "dossier" / "sortie"), str(tmp_path / "travail"), str(tmp_path / "cache"), racine=r,
               environ={}, journal=lambda *a: None, progres=Progres(ecrire=lambda l: None, intervalle=60),
               entrees_relief=entrees_relief(jeu, "tmp"))
    assert vus and all(v == (str(tmp_path / "travail" / "tmp"),) * 4 for v in vus)
    assert (tempfile.gettempdir(), os.environ.get("TMPDIR")) == avant, "remis comme avant"


# ---------------------------------------------------------------------- `fabriquer` : les droits aussi
def test_fabriquer_pose_les_droits_sous_umask_077(jeu, tmp_path):
    ancien = os.umask(0o077)
    try:
        out = str(tmp_path / "f")
        fab.fabriquer(jeu["emprise"], [jeu["osm"]], str(tmp_path / "c"), out, journal=lambda *a: None,
                      entrees_relief=entrees_relief(jeu, "u"))
    finally:
        os.umask(ancien)
    for r, ds, fs in os.walk(out):
        assert oct(os.stat(r).st_mode & 0o777) == "0o755", r
        for f in fs:
            assert oct(os.stat(os.path.join(r, f)).st_mode & 0o777) == "0o644", f
    ok, res = controles(out, journal=lambda *a: None)
    assert ok, res["echecs"]


def test_poser_droits_refuse_un_lien_symbolique(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    os.symlink("/etc/passwd", d / "lien")
    with pytest.raises(fab.SortieErreur, match="lien symbolique"):
        fab.poser_droits(str(d))


# ---------------------------------------------------------------------- `verifier` étendu : chaque refus
@pytest.fixture
def copie(reussite, tmp_path):
    d = str(tmp_path / "copie")
    shutil.copytree(reussite["sortie"], d, symlinks=True)
    return d


def echecs(d):
    ok, res = controles(d, journal=lambda *a: None)
    assert not ok
    return res["echecs"]


def ecrire_style(d, nom, f):
    p = os.path.join(d, nom)
    s = json.load(open(p))
    f(s)
    os.chmod(p, 0o644)
    json.dump(s, open(p, "w"))
    os.chmod(p, 0o644)


def test_verifier_passe_sur_la_copie(copie):
    ok, res = controles(copie, journal=lambda *a: None)
    assert ok, res["echecs"]


def test_verifier_refuse_une_entree_de_trop(copie):
    open(os.path.join(copie, "intrus.txt"), "w").close()
    assert any("entrée de trop à la racine : intrus.txt" in e for e in echecs(copie))


def test_verifier_refuse_un_dossier_de_trop(copie):
    os.mkdir(os.path.join(copie, "cache"))
    assert any("entrée de trop à la racine : cache" in e for e in echecs(copie))


def test_verifier_refuse_un_fichier_absent_et_un_fichier_a_la_place_d_un_dossier(copie):
    os.remove(os.path.join(copie, "LICENCE.txt"))
    assert any("fichier absent : LICENCE.txt" in e for e in echecs(copie))
    shutil.rmtree(os.path.join(copie, "sprite"))
    open(os.path.join(copie, "sprite"), "w").close()
    assert any("sprite doit être un dossier" in e for e in echecs(copie))


def test_verifier_refuse_un_lien_symbolique(copie):
    os.symlink("manifeste.json", os.path.join(copie, "lien.json"))
    assert any("lien symbolique : lien.json" in e for e in echecs(copie))


def test_verifier_refuse_un_lien_symbolique_au_lieu_d_un_fichier_attendu(copie, tmp_path):
    p = os.path.join(copie, "LICENCE.txt")
    cible = str(tmp_path / "ailleurs.txt")
    shutil.move(p, cible)
    os.symlink(cible, p)
    assert any("lien symbolique : LICENCE.txt" in e for e in echecs(copie))


def test_verifier_refuse_un_lien_symbolique_dans_un_sous_dossier(copie):
    os.symlink("papier.json", os.path.join(copie, "sprite", "lien.json"))
    assert any("lien symbolique : sprite/lien.json" in e for e in echecs(copie))


def test_verifier_refuse_un_lien_symbolique_vers_un_dossier(copie, tmp_path):
    os.symlink(str(tmp_path), os.path.join(copie, "glyphs", "dehors"))
    assert any("lien symbolique : glyphs/dehors" in e for e in echecs(copie))


def test_verifier_refuse_des_droits_de_fichier(copie):
    os.chmod(os.path.join(copie, "fond.pmtiles"), 0o600)
    assert any("droits 600 au lieu de 644 : fond.pmtiles" in e for e in echecs(copie))
    os.chmod(os.path.join(copie, "fond.pmtiles"), 0o664)
    assert any("droits 664 au lieu de 644 : fond.pmtiles" in e for e in echecs(copie))


def test_verifier_refuse_des_droits_de_dossier(copie):
    os.chmod(os.path.join(copie, "sprite"), 0o700)
    assert any("droits 700 au lieu de 755 : sprite" in e for e in echecs(copie))
    os.chmod(os.path.join(copie, "sprite"), 0o755)
    os.chmod(copie, 0o700)
    assert any("la racine du dossier" in e for e in echecs(copie))


def test_verifier_refuse_une_adresse_absolue_dans_un_style(copie):
    ecrire_style(copie, "style-clair.json", lambda s: s["sources"]["fond"].__setitem__("url", "/fond.pmtiles"))
    assert any("style clair : adresse non relative : source fond" in e for e in echecs(copie))


@pytest.mark.parametrize("cle, valeur", [("sprite", "/sprite/papier"), ("sprite", "https://exemple.invalid/papier"),
                                          ("glyphs", "/glyphs/{fontstack}/{range}.pbf"),
                                          ("glyphs", "http://exemple.invalid/{fontstack}/{range}.pbf"),
                                          ("glyphs", "sprite/{fontstack}.pbf")])
def test_verifier_refuse_un_sprite_ou_des_glyphes_non_relatifs(copie, cle, valeur):
    ecrire_style(copie, "style-sombre.json", lambda s: s.__setitem__(cle, valeur))
    assert any(("style sombre : adresse non relative : %s" % cle) in e for e in echecs(copie))


def test_verifier_refuse_une_source_avec_un_autre_schema(copie):
    ecrire_style(copie, "style-clair.json", lambda s: s["sources"]["relief"].__setitem__("url", "https://exemple.invalid/r.pmtiles"))
    assert any("source relief" in e for e in echecs(copie))
    ecrire_style(copie, "style-clair.json", lambda s: s["sources"]["relief"].__setitem__("url", "pmtiles://https://x/r.pmtiles"))
    assert any("source relief" in e for e in echecs(copie))


def test_verifier_refuse_un_manifeste_sans_commit(copie):
    p = os.path.join(copie, "manifeste.json")
    m = json.load(open(p))
    del m["fabrique"]["commit"]
    json.dump(m, open(p, "w"))
    assert any("fabrique.commit" in e for e in echecs(copie))
    m["fabrique"]["commit"] = ""
    json.dump(m, open(p, "w"))
    assert any("fabrique.commit" in e for e in echecs(copie))
    del m["fabrique"]
    json.dump(m, open(p, "w"))
    assert any("fabrique.commit" in e for e in echecs(copie))


def test_la_commande_verifier_rend_1_sur_un_refus_et_0_sur_un_bon_dossier(copie, capsys):
    assert cli.principal(["verifier", copie]) == 0
    open(os.path.join(copie, "intrus.txt"), "w").close()
    assert cli.principal(["verifier", copie]) == 1


def test_verifier_refuse_un_dossier_qui_n_en_est_pas_un(tmp_path):
    assert any("pas un dossier" in e for e in echecs(str(tmp_path / "absent")))


# ---------------------------------------------------------------------- les dépendances d'exécution
def lire_pins(chemin):
    out = {}
    for l in open(chemin, encoding="utf-8"):
        l = l.split("#")[0].strip()
        if l:
            assert "==" in l and not any(c in l for c in "<>~!;["), l
            n, v = l.split("==")
            out[n.strip().lower().replace("_", "-")] = v.strip()
    return out


def test_requirements_fabrique_meme_version_que_requirements():
    dev = lire_pins(os.path.join(RACINE, "requirements.txt"))
    run = lire_pins(os.path.join(RACINE, "requirements-fabrique.txt"))
    assert run and set(run) <= set(dev)
    for n, v in run.items():
        assert dev[n] == v, n
    for interdit in ("playwright", "pytest", "pyflakes", "greenlet", "pluggy", "iniconfig", "pygments"):
        assert interdit not in run, interdit


def test_requirements_fabrique_est_une_liste_fermee():
    """Toutes les dépendances (transitives comprises) de chaque paquet de la liste sont dans la liste."""
    import importlib.metadata as md
    from packaging.requirements import Requirement
    run = lire_pins(os.path.join(RACINE, "requirements-fabrique.txt"))
    for n in run:
        for r in md.requires(n) or []:
            req = Requirement(r)
            if req.marker is not None and not req.marker.evaluate({"extra": ""}):
                continue
            assert req.name.lower().replace("_", "-") in run, "%s demande %s, absent de la liste" % (n, req.name)


def test_les_modules_de_la_fabrique_n_importent_que_la_liste():
    """Chaque module importé par `fabrique` (hors bibliothèque standard) est fourni par un paquet de la liste."""
    import ast
    import importlib.metadata as md
    run = lire_pins(os.path.join(RACINE, "requirements-fabrique.txt"))
    paquets_de = {}
    for d in md.distributions():
        nom = d.metadata["Name"].lower().replace("_", "-")
        for f in d.files or []:
            top = str(f).split("/")[0].split(".")[0]
            paquets_de.setdefault(top, set()).add(nom)
    stdlib = set(sys.stdlib_module_names)
    for f in os.listdir(os.path.join(RACINE, "fabrique")):
        if not f.endswith(".py"):
            continue
        arbre = ast.parse(open(os.path.join(RACINE, "fabrique", f), encoding="utf-8").read())
        for n in ast.walk(arbre):
            mods = []
            if isinstance(n, ast.Import):
                mods = [a.name.split(".")[0] for a in n.names]
            elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
                mods = [n.module.split(".")[0]]
            for m in mods:
                if m in stdlib or m == "fabrique":
                    continue
                assert paquets_de.get(m, set()) & set(run), "%s (importé par fabrique/%s) n'est fourni par aucun paquet de requirements-fabrique.txt" % (m, f)

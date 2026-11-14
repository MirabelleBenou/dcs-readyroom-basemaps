"""La commande `theatre` : fabriquer un théâtre entier, sans surveillance, dans un conteneur verrouillé.

    python -m fabrique theatre <nom> --sortie <dossier> --travail <dossier> --cache <dossier>

Le descripteur `theatres/<nom>.json` dit l'emprise (sous `zones/`), les extraits OpenStreetMap (adresses HTTPS) et, si
besoin, le zoom pivot. Trois dossiers : `--sortie` (n'existe pas encore ; son parent, si), `--travail` (existe, vide ; on
y laisse tout ce qui est temporaire, et on le vide en partant, même sur une erreur), `--cache` (existe ; les extraits et
les dalles qu'on y range se gardent d'une fabrication à l'autre). Rien ne s'écrit ailleurs que sous ces trois dossiers
et `/tmp`. Le réseau : HTTPS, vers les adresses du descripteur et les seaux Copernicus ; jamais Overpass.

Le code de sortie est 0 si et seulement si le dossier est complet, vérifié (les contrôles de `verifier`) et placé.
"""
import errno
import hashlib
import json
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request

import osmium

from . import fabriquer as fab
from . import grille as mgrs
from . import manifeste, verifier as ver
from .emprise import lire_emprise
from .progres import Progres
from .sources import USER_AGENT, preparer_dalles, sha256_fichier

RACINE = fab.RACINE
NOM = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
EXTENSIONS = (".osm.pbf", ".osm.gz", ".osm")
FICHIER_URL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
CLES = ("nom", "emprise", "osm", "bloc_zoom")
CLES_OBLIGATOIRES = ("nom", "emprise", "osm")


class TheatreErreur(RuntimeError):
    pass


class Descripteur:
    def __init__(self, nom, emprise_relative, emprise, osm, bloc_zoom, em_wgs):
        self.nom, self.emprise_relative, self.emprise = nom, emprise_relative, emprise
        self.osm, self.bloc_zoom, self.em_wgs = osm, bloc_zoom, em_wgs


def nom_fichier_url(url):
    return url.rsplit("/", 1)[-1]


def _verifier_adresse(url):
    if not isinstance(url, str) or not url.startswith("https://"):
        raise TheatreErreur("adresse OSM refusée (HTTPS seulement) : %r" % (url,))
    p = urllib.parse.urlsplit(url)
    if not p.hostname or p.username is not None or p.password is not None or "@" in p.netloc:
        raise TheatreErreur("adresse OSM refusée (hôte absent, ou identifiants dans l'adresse) : %r" % (url,))
    if p.query or p.fragment or "?" in url or "#" in url:
        raise TheatreErreur("adresse OSM refusée (ni paramètres ni ancre) : %r" % (url,))
    if not url.endswith(EXTENSIONS):
        raise TheatreErreur("adresse OSM refusée (.osm.pbf, .osm.gz ou .osm attendu) : %r" % (url,))
    if not FICHIER_URL.fullmatch(nom_fichier_url(url)):
        raise TheatreErreur("adresse OSM refusée (nom de fichier inutilisable) : %r" % (url,))


def charger_descripteur(nom, racine=RACINE):
    """Lit et valide `theatres/<nom>.json` (clés exactes, adresses HTTPS, emprise sous `zones/` lisible, hémisphère nord).
    Rend un `Descripteur`. Toute faute : `TheatreErreur`, avant que rien ne soit écrit."""
    if not isinstance(nom, str) or not NOM.fullmatch(nom):
        raise TheatreErreur("nom de théâtre invalide : %r (attendu : %s)" % (nom, NOM.pattern))
    chemin = os.path.join(racine, "theatres", nom + ".json")
    if not os.path.isfile(chemin):
        raise TheatreErreur("théâtre inconnu : %s (pas de theatres/%s.json)" % (nom, nom))
    try:
        with open(chemin, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError) as e:
        raise TheatreErreur("descripteur illisible : theatres/%s.json (%s)" % (nom, e))
    if not isinstance(d, dict):
        raise TheatreErreur("descripteur : un objet JSON est attendu")
    inconnues = sorted(set(d) - set(CLES))
    if inconnues:
        raise TheatreErreur("descripteur : clé inconnue : %s" % ", ".join(inconnues))
    manquantes = [c for c in CLES_OBLIGATOIRES if c not in d]
    if manquantes:
        raise TheatreErreur("descripteur : clé manquante : %s" % ", ".join(manquantes))
    if d["nom"] != nom:
        raise TheatreErreur("descripteur : `nom` (%r) n'égale pas le nom du fichier (%r)" % (d["nom"], nom))
    rel = d["emprise"]
    if not isinstance(rel, str) or not rel or os.path.isabs(rel) or "\\" in rel:
        raise TheatreErreur("descripteur : `emprise` doit être un chemin relatif sous zones/ : %r" % (rel,))
    norm = os.path.normpath(rel)
    if norm.split(os.sep)[0] != "zones" or len(norm.split(os.sep)) < 2 or ".." in norm.split(os.sep):
        raise TheatreErreur("descripteur : `emprise` doit être sous zones/ : %r" % (rel,))
    emprise = os.path.join(racine, norm)
    zones = os.path.realpath(os.path.join(racine, "zones"))
    if not os.path.realpath(emprise).startswith(zones + os.sep) or not os.path.isfile(emprise):
        raise TheatreErreur("descripteur : emprise introuvable sous zones/ : %r" % (rel,))
    osm = d["osm"]
    if not isinstance(osm, list) or not osm:
        raise TheatreErreur("descripteur : `osm` doit être une liste non vide d'adresses")
    for u in osm:
        _verifier_adresse(u)
    bz = d.get("bloc_zoom", fab.BLOC_ZOOM_DEFAUT)
    if isinstance(bz, bool) or not isinstance(bz, int) or not fab.BLOC_ZOOM_MIN <= bz <= fab.BLOC_ZOOM_MAX:
        raise TheatreErreur("descripteur : `bloc_zoom` doit être un entier de %d à %d : %r"
                            % (fab.BLOC_ZOOM_MIN, fab.BLOC_ZOOM_MAX, bz))
    try:
        em_wgs = lire_emprise(emprise)
        mgrs.verifier_hemisphere(em_wgs)
    except Exception as e:
        raise TheatreErreur("descripteur : emprise inutilisable (%s)" % e)
    return Descripteur(nom, rel, emprise, list(osm), bz, em_wgs)


def lister_descripteurs(racine=RACINE):
    """Les noms de tous les descripteurs de `theatres/`."""
    dossier = os.path.join(racine, "theatres")
    return sorted(f[:-5] for f in os.listdir(dossier) if f.endswith(".json"))


def commit_donne(environ):
    """`FABRIQUE_COMMIT` s'il est posé : 40 hexadécimaux minuscules, sinon refus. Rend {commit, modifie} ou None."""
    if "FABRIQUE_COMMIT" not in environ:
        return None
    v = environ["FABRIQUE_COMMIT"]
    if not COMMIT.fullmatch(v):
        raise TheatreErreur("FABRIQUE_COMMIT doit être 40 hexadécimaux minuscules")
    return {"commit": v, "modifie": False}


# ---------------------------------------------------------------------- les extraits OSM
def _hote(url):
    p = urllib.parse.urlsplit(url)
    return (p.hostname or "").lower(), p.port or 443


class _RedirectionMemeHoteHttps(urllib.request.HTTPRedirectHandler):
    """Ne suit une redirection que vers HTTPS et vers le même hôte (et le même port) que la requête qui la reçoit ; comme
    chaque redirection suivie garde l'hôte, toute la chaîne reste chez l'hôte de l'adresse demandée."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        nouvelle = urllib.parse.urljoin(req.full_url, newurl)
        if not nouvelle.startswith("https://"):
            raise urllib.error.URLError("redirection vers une adresse qui n'est pas en HTTPS : refusée")
        if _hote(nouvelle) != _hote(req.full_url):
            raise urllib.error.URLError("redirection vers un autre hôte (%s) : refusée" % _hote(nouvelle)[0])
        return super().redirect_request(req, fp, code, msg, headers, nouvelle)


def ouvrir_https(req, timeout=None):
    """`urlopen`, qui ne suit aucune redirection hors de HTTPS ni vers un autre hôte que celui de l'adresse demandée."""
    return urllib.request.build_opener(_RedirectionMemeHoteHttps).open(req, timeout=timeout)


def chemin_osm(cache, url):
    """`osm/<16 premiers hexadécimaux du sha256 de l'adresse>-<nom de fichier de l'adresse>` sous le cache."""
    return os.path.join(cache, "osm", "%s-%s" % (hashlib.sha256(url.encode("utf-8")).hexdigest()[:16],
                                                 nom_fichier_url(url)))


def _format_osmium(nom):
    for ext in EXTENSIONS:
        if nom.endswith(ext):
            return ext[1:]
    raise TheatreErreur("format OSM inconnu : %s" % nom)


def en_tete_lisible(chemin, nom):
    """L'en-tête de l'extrait se lit avec pyosmium (le format vient du nom `nom`, pas de `chemin` : un `.part`)."""
    lecteur = osmium.io.Reader(osmium.io.File(chemin, _format_osmium(nom)), osmium.osm.osm_entity_bits.NOTHING)
    try:
        lecteur.header()
    finally:
        lecteur.close()


def obtenir_osm(url, cache, ouvrir=ouvrir_https):
    """Le fichier de l'extrait dans le cache. Présent : rendu sans toucher le réseau. Absent : téléchargé en HTTPS dans un
    `.part` du même dossier (code 200, taille égale à Content-Length s'il y en a une, en-tête lisible), puis placé par
    `os.replace`. Toute erreur efface le `.part` et fait échouer la fabrication."""
    destination = chemin_osm(cache, url)
    if os.path.isfile(destination):
        return destination
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    part = destination + ".part"
    nom = nom_fichier_url(url)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with ouvrir(req, timeout=300) as r:
            code = getattr(r, "status", 200)
            if code != 200:
                raise TheatreErreur("extrait %s : HTTP %s" % (nom, code))
            attendu = r.headers.get("Content-Length")
            with open(part, "wb") as f:
                shutil.copyfileobj(r, f, 1 << 20)
        if attendu is not None and os.path.getsize(part) != int(attendu):
            raise TheatreErreur("extrait %s : réponse tronquée (%d octets sur %s)" % (nom, os.path.getsize(part), attendu))
        try:
            en_tete_lisible(part, nom)
        except RuntimeError as e:
            raise TheatreErreur("extrait %s : en-tête illisible (%s)" % (nom, e))
        os.replace(part, destination)
    except TheatreErreur:
        raise
    except Exception as e:
        raise TheatreErreur("extrait %s : téléchargement impossible (%s : %s)" % (nom, type(e).__name__, e))
    finally:
        if os.path.exists(part):
            os.remove(part)
    return destination


# ---------------------------------------------------------------------- la fabrication
def vider(dossier):
    """Vide le dossier (le dossier lui-même reste) ; ne suit aucun lien."""
    for e in os.scandir(dossier):
        if e.is_dir(follow_symlinks=False):
            shutil.rmtree(e.path, ignore_errors=True)
        else:
            try:
                os.unlink(e.path)
            except OSError:
                pass


def _sous(a, b):
    """`a` est `b` ou se trouve sous `b` (chemins réels)."""
    return a == b or a.startswith(b.rstrip(os.sep) + os.sep)


def commit_a_ecrire(environ):
    """Le commit du manifeste : `FABRIQUE_COMMIT` s'il est posé, sinon celui que Git donne. Dans les deux cas : 40
    hexadécimaux minuscules, sinon refus (un théâtre ne se fabrique pas sans commit nommé). Rend {commit, modifie}."""
    commit = commit_donne(environ)
    if commit is None:
        commit = manifeste.commit_fabrique(RACINE)
        if not isinstance(commit.get("commit"), str) or not COMMIT.fullmatch(commit["commit"]):
            raise TheatreErreur("pas de commit nommé : FABRIQUE_COMMIT n'est pas posée et Git ne donne pas un commit de 40 "
                                "hexadécimaux (%r)" % (commit.get("commit"),))
    return commit


def verifier_avant_d_ecrire(desc_nom, sortie, travail, cache, environ, racine):
    """Tous les refus d'avant écriture. Rend (descripteur, commit à écrire)."""
    desc = charger_descripteur(desc_nom, racine)
    commit = commit_a_ecrire(environ)
    if os.path.lexists(sortie):
        raise TheatreErreur("--sortie existe déjà : %s" % sortie)
    parent = os.path.dirname(sortie)
    if not os.path.isdir(parent):
        raise TheatreErreur("le parent de --sortie n'existe pas : %s" % parent)
    if not os.path.isdir(travail):
        raise TheatreErreur("--travail n'existe pas (ou n'est pas un dossier) : %s" % travail)
    if not os.path.isdir(cache):
        raise TheatreErreur("--cache n'existe pas (ou n'est pas un dossier) : %s" % cache)
    # les trois dossiers ne se recouvrent pas (chemins réels : un lien ne contourne rien)
    r_sortie = os.path.join(os.path.realpath(parent), os.path.basename(sortie))
    r_travail, r_cache = os.path.realpath(travail), os.path.realpath(cache)
    if _sous(r_sortie, r_travail):
        raise TheatreErreur("--sortie est sous --travail (%s) : la sortie serait vidée avec le travail" % r_sortie)
    if _sous(r_sortie, r_cache):
        raise TheatreErreur("--sortie est sous --cache (%s)" % r_sortie)
    if _sous(r_travail, r_cache) or _sous(r_cache, r_travail):
        raise TheatreErreur("--travail et --cache se recouvrent (%s, %s) : le travail vidé emporterait le cache"
                            % (r_travail, r_cache))
    if os.listdir(travail):
        raise TheatreErreur("--travail n'est pas vide : %s" % travail)
    if os.stat(travail).st_dev != os.stat(parent).st_dev:
        raise TheatreErreur("--travail et le parent de --sortie ne sont pas sur le même système de fichiers : la sortie "
                            "ne peut pas apparaître d'un seul `os.replace`")
    return desc, commit


CACHE_GDAL_MIO = "64"       # le cache de blocs de GDAL : 5 % de la mémoire de la machine par défaut, ici borné et fixe


class _Temporaires:
    """Tout ce qui est temporaire passe sous `--travail` : `tempfile`, SQLite, GDAL. Le cache de blocs de GDAL est borné
    (sauf si `GDAL_CACHEMAX` est posée) : la mémoire ne dépend pas de celle de la machine. Remis comme avant en sortant."""
    VARIABLES = ("TMPDIR", "SQLITE_TMPDIR", "CPL_TMPDIR", "GDAL_CACHEMAX")

    def __init__(self, dossier):
        self.dossier = dossier

    def __enter__(self):
        os.makedirs(self.dossier, exist_ok=True)
        self.avant = (tempfile.tempdir, {v: os.environ.get(v) for v in self.VARIABLES})
        tempfile.tempdir = self.dossier
        for v in self.VARIABLES:
            if v == "GDAL_CACHEMAX":
                os.environ.setdefault(v, CACHE_GDAL_MIO)
            else:
                os.environ[v] = self.dossier
        return self

    def __exit__(self, *exc):
        tempfile.tempdir = self.avant[0]
        for v, val in self.avant[1].items():
            if val is None:
                os.environ.pop(v, None)
            else:
                os.environ[v] = val
        return False


def theatre(nom, sortie, travail, cache, racine=RACINE, environ=None, journal=None, progres=None, ouvrir=ouvrir_https,
            entrees_relief=None):
    """Fabrique le théâtre `nom`. `progres` : un `Progres` (par défaut, sur la sortie standard) ; `ouvrir`,
    `entrees_relief` : pour les essais (réseau, dalles)."""
    environ = os.environ if environ is None else environ
    journal = journal if journal is not None else (lambda *a: print(*a, flush=True))
    sortie, travail, cache = (os.path.abspath(p) for p in (sortie, travail, cache))
    desc, commit = verifier_avant_d_ecrire(nom, sortie, travail, cache, environ, racine)
    progres = progres if progres is not None else Progres()
    try:
        with progres, _Temporaires(os.path.join(travail, "tmp")):
            en_cours = os.path.join(travail, "en-cours")
            os.makedirs(en_cours)
            os.makedirs(os.path.join(travail, "travail"))
            fichiers = []
            for k, url in enumerate(desc.osm, 1):
                progres.etape("extraits OpenStreetMap : %s" % nom_fichier_url(url), k, len(desc.osm))
                fichiers.append(obtenir_osm(url, cache, ouvrir))
            progres.etape("empreintes des extraits OpenStreetMap")
            entrees_osm = []
            for url, f in zip(desc.osm, fichiers):
                entrees_osm.append({"adresse": url, "fichier": nom_fichier_url(url), "taille": os.path.getsize(f),
                                    "sha256": sha256_fichier(f)})
            if entrees_relief is None:
                # les dalles passent par le même `ouvrir` que les extraits : aucune redirection hors HTTPS ni vers un autre
                # hôte (`fabriquer` et `sources.py` ne changent pas : on leur donne les dalles prêtes)
                progres.etape("dalles du relief")
                entrees_relief = preparer_dalles(desc.em_wgs.bounds, os.path.join(cache, "relief"), ouvrir)
            res = fab._fabriquer(desc.emprise, desc.em_wgs, fichiers, os.path.join(cache, "relief"), en_cours,
                                 os.path.join(travail, "travail"), journal, entrees_relief, desc.bloc_zoom,
                                 progres=progres, entrees_osm=entrees_osm, commit=commit, theatre=nom)
            fab.poser_droits(en_cours)
            progres.etape("vérification du dossier")
            ok, r = ver.controles(en_cours, journal)
            if not ok:
                raise TheatreErreur("la vérification du dossier échoue (%d) : %s"
                                    % (len(r["echecs"]), " ; ".join(r["echecs"][:5])))
            with open(os.path.join(en_cours, "manifeste.json"), encoding="utf-8") as f:
                nomme = json.load(f).get("fabrique", {}).get("commit")
            if not isinstance(nomme, str) or not COMMIT.fullmatch(nomme):
                raise TheatreErreur("le manifeste ne porte pas un fabrique.commit de 40 hexadécimaux minuscules : %r" % (nomme,))
            progres.etape("placement de la sortie")
            try:
                os.replace(en_cours, sortie)
            except OSError as e:
                if e.errno == errno.EXDEV:
                    raise TheatreErreur("--travail et --sortie ne sont pas sur le même système de fichiers : %s" % e)
                raise
            progres.etape("terminé")
            return res
    finally:
        vider(travail)
        restes = sorted(os.listdir(travail))
        if restes:
            # même après un placement réussi : un dossier de travail qui garde des restes est un échec
            raise TheatreErreur("--travail n'est pas vide après le nettoyage (%d entrées : %s)"
                                % (len(restes), ", ".join(restes[:5])))

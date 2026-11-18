"""Un extrait OSM redirigé vers un miroir (autre hôte, HTTPS) : suivi, puis gardé seulement si son MD5 est celui de l'hôte d'origine."""
import hashlib
import os
import urllib.error
import urllib.request

import pytest

from fabrique import theatre as th
from fabrique.progres import Progres
from fabrique.theatre import TheatreErreur
from test_theatre import Reponse, URL, appeler, espace, un_osm  # noqa: F401  (espace : fixture)

NOM = "mini.osm"
MIROIR = "https://miroir.invalid/pub/mini.osm"


class ReponseMiroir(Reponse):
    """Une réponse qui dit son adresse finale (`geturl`), comme celle d'`urllib` après une redirection."""

    def __init__(self, contenu, finale, **kw):
        super().__init__(contenu, **kw)
        self.finale = finale

    def geturl(self):
        return self.finale


def serveur(brut, md5=None, finale=MIROIR, url=URL):
    """Un `ouvrir` factice : l'extrait vient de `finale`, le `.md5` de `md5` (octets, ou une exception, ou une Reponse)."""
    vus = []

    def ouvrir(req, timeout=None):
        vus.append(req.full_url)
        if req.full_url == url:
            return ReponseMiroir(brut, finale)
        assert req.full_url == url + ".md5", req.full_url
        if isinstance(md5, Exception):
            raise md5
        return md5 if isinstance(md5, Reponse) else Reponse(md5)
    ouvrir.vus = vus
    return ouvrir


def ligne(brut, nom=NOM, sep="  "):
    return ("%s%s%s\n" % (hashlib.md5(brut).hexdigest(), sep, nom)).encode()


def lancer(tmp_path, ouvrir, signaler=None):
    cache = tmp_path / "cache"
    cache.mkdir(exist_ok=True)
    return str(cache), lambda: th.obtenir_osm(URL, str(cache), ouvrir=ouvrir, signaler=signaler)


def test_redirection_vers_un_miroir_avec_md5_conforme(tmp_path):
    brut = open(un_osm(tmp_path), "rb").read()
    ouvrir = serveur(brut, ligne(brut))
    dits = []
    cache, go = lancer(tmp_path, ouvrir, lambda n, h: dits.append((n, h)))
    f = go()
    assert open(f, "rb").read() == brut and f == th.chemin_osm(cache, URL)
    assert ouvrir.vus == [URL, URL + ".md5"], "une seule requête .md5, à l'hôte d'origine"
    assert dits == [(NOM, "miroir.invalid")]
    assert [n for n in os.listdir(os.path.join(cache, "osm")) if n.endswith(".part")] == []


def test_md5_en_majuscules_et_separateur_quelconque(tmp_path):
    brut = open(un_osm(tmp_path), "rb").read()
    md5 = ("%s \t %s" % (hashlib.md5(brut).hexdigest().upper(), NOM)).encode()
    _, go = lancer(tmp_path, serveur(brut, md5))
    assert os.path.isfile(go())


def refuse_et_nettoie(tmp_path, ouvrir, message):
    cache, go = lancer(tmp_path, ouvrir)
    with pytest.raises(TheatreErreur, match=message) as e:
        go()
    assert NOM in str(e.value)
    assert os.listdir(os.path.join(cache, "osm")) == [], "ni `.part` ni rien au cache"
    return str(e.value)


def test_md5_different(tmp_path):
    brut = open(un_osm(tmp_path), "rb").read()
    faux = hashlib.md5(b"autre chose").hexdigest()
    m = refuse_et_nettoie(tmp_path, serveur(brut, ("%s  %s\n" % (faux, NOM)).encode()), "diffère")
    assert faux in m and hashlib.md5(brut).hexdigest() in m, "les deux sommes sont dans le message"


def test_md5_introuvable_404(tmp_path):
    brut = open(un_osm(tmp_path), "rb").read()
    e404 = urllib.error.HTTPError(URL + ".md5", 404, "Not Found", {}, None)
    refuse_et_nettoie(tmp_path, serveur(brut, e404), "MD5 introuvable")
    refuse_et_nettoie(tmp_path, serveur(brut, Reponse(b"x", status=404)), "MD5 introuvable")


def test_md5_introuvable_erreur_reseau(tmp_path):
    brut = open(un_osm(tmp_path), "rb").read()
    refuse_et_nettoie(tmp_path, serveur(brut, urllib.error.URLError("pas de réseau")), "MD5 introuvable")


@pytest.mark.parametrize("contenu", [b"", b"pas une somme\n", b"abc123  mini.osm\n", b"z" * 32 + b"  mini.osm\n",
                                     b"0" * 32 + b"\n", b"0" * 32 + b"mini.osm\n", b"0" * 33 + b"  mini.osm\n"])
def test_md5_mal_forme(tmp_path, contenu):
    brut = open(un_osm(tmp_path), "rb").read()
    refuse_et_nettoie(tmp_path, serveur(brut, contenu), "mal formée")


def test_md5_de_deux_lignes_est_mal_forme(tmp_path):
    brut = open(un_osm(tmp_path), "rb").read()
    refuse_et_nettoie(tmp_path, serveur(brut, ligne(brut) + ligne(brut)), "mal formée")


def test_md5_d_un_autre_nom_de_fichier(tmp_path):
    brut = open(un_osm(tmp_path), "rb").read()
    refuse_et_nettoie(tmp_path, serveur(brut, ligne(brut, nom="autre.osm")), "autre fichier")


def test_les_controles_actuels_passent_avant_le_md5(tmp_path):
    """Une réponse tronquée échoue sur la taille : la somme n'est même pas demandée."""
    brut = open(un_osm(tmp_path), "rb").read()

    def ouvrir(req, timeout=None):
        assert req.full_url == URL
        return ReponseMiroir(brut, MIROIR, longueur=len(brut) + 5)
    refuse_et_nettoie(tmp_path, ouvrir, "tronquée")


def test_un_extrait_sans_redirection_n_a_pas_de_md5(tmp_path):
    brut = open(un_osm(tmp_path), "rb").read()
    vus, dits = [], []

    def ouvrir(req, timeout=None):
        vus.append(req.full_url)
        return Reponse(brut)                        # aucune adresse finale : servi par l'hôte de l'adresse
    cache, go = lancer(tmp_path, ouvrir, lambda *a: dits.append(a))
    go()
    assert vus == [URL] and dits == []


def test_une_redirection_vers_le_meme_hote_n_a_pas_de_md5(tmp_path):
    brut = open(un_osm(tmp_path), "rb").read()
    vus = []

    def ouvrir(req, timeout=None):
        vus.append(req.full_url)
        return ReponseMiroir(brut, "https://EXEMPLE.invalid:443/ailleurs/mini.osm")
    _, go = lancer(tmp_path, ouvrir)
    go()
    assert vus == [URL]


def redirige_extrait(depuis, vers):
    h = th._RedirectionExtraitHttps()
    return h.redirect_request(urllib.request.Request(depuis), None, 302, "Found", {}, vers)


@pytest.mark.parametrize("vers", ["https://miroir.invalid/b.osm", "https://miroir.invalid:443/b.osm", "/chemin/b.osm",
                                  "https://exemple.invalid/b.osm"])
def test_redirection_d_extrait_suivie(vers):
    assert redirige_extrait("https://exemple.invalid/a.osm", vers) is not None


@pytest.mark.parametrize("vers,mot", [("http://miroir.invalid/b.osm", "HTTPS"), ("ftp://miroir.invalid/b.osm", "HTTPS"),
                                      ("https://miroir.invalid:8443/b.osm", "port"),
                                      ("https://exemple.invalid:8443/b.osm", "port"),
                                      ("https://moi@miroir.invalid/b.osm", "identifiants"),
                                      ("https://moi:mdp@miroir.invalid/b.osm", "identifiants"),
                                      ("https://:mdp@miroir.invalid/b.osm", "identifiants")])
def test_redirection_d_extrait_refusee(vers, mot):
    with pytest.raises(urllib.error.URLError, match=mot):
        redirige_extrait("https://exemple.invalid/a.osm", vers)


def test_une_redirection_refusee_fait_echouer_l_extrait_en_la_nommant(tmp_path):
    # via la vraie chaîne d'`obtenir_osm` : l'erreur de l'ouvreur devient une TheatreErreur qui nomme l'extrait
    def ouvrir_refus(req, timeout=None):
        raise urllib.error.URLError("redirection vers une adresse qui n'est pas en HTTPS : refusée")
    refuse_et_nettoie(tmp_path, ouvrir_refus, "pas en HTTPS")


def test_les_dalles_gardent_l_ouvreur_strict(espace, monkeypatch, jeu):    # noqa: F811
    vus = []

    def faux(boite, cache, ouvrir=None):
        vus.append(ouvrir)
        raise StopIteration

    monkeypatch.setattr(th, "preparer_dalles", faux)
    os.makedirs(os.path.join(espace["cache"], "osm"))
    import shutil
    shutil.copy(jeu["osm"], th.chemin_osm(espace["cache"], URL))
    with pytest.raises(StopIteration):
        appeler(espace)
    assert vus == [th.ouvrir_https]
    with pytest.raises(urllib.error.URLError, match="autre hôte"):
        th._RedirectionMemeHoteHttps().redirect_request(
            urllib.request.Request("https://exemple.invalid/t.tif"), None, 302, "Found", {}, "https://miroir.invalid/t.tif")


def test_la_ligne_de_journal_de_theatre(espace, monkeypatch, jeu):    # noqa: F811
    brut = open(jeu["osm"], "rb").read()
    lignes = []
    monkeypatch.setattr(th, "preparer_dalles", lambda *a, **k: (_ for _ in ()).throw(StopIteration()))
    ouvrir = serveur(brut, ligne(brut))
    with pytest.raises(StopIteration):
        th.theatre("mini", espace["sortie"], espace["travail"], espace["cache"], racine=espace["racine"],
                   environ={"FABRIQUE_COMMIT": "0123456789abcdef0123456789abcdef01234567"},
                   progres=Progres(ecrire=lignes.append, intervalle=60), journal=lambda *a: None, ouvrir=ouvrir)
    assert "progression : extraits OpenStreetMap : mini.osm servi par miroir.invalid, MD5 vérifié" in lignes
    assert all("http" not in l for l in lignes)

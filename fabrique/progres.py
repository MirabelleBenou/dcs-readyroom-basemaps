"""La progression d'une fabrication sans surveillance : des lignes `progression : <étape> <n>/<total>` sur la sortie standard.

Une ligne à chaque changement d'étape, à chaque bloc (ou bande) et, quoi qu'il arrive, au plus `intervalle` secondes après
la précédente : un battement, qui redit l'étape en cours et le temps écoulé. Le battement est un fil à part ; l'horloge et
l'intervalle s'injectent (essais). Aucune adresse n'y passe : seulement des noms d'étapes et des comptes.
"""
import sys
import threading
import time

INTERVALLE_DEFAUT = 20.0       # secondes ; le contrat exige au plus 60 s entre deux lignes
PREFIXE = "progression : "


def _ecrire_stdout(ligne):
    sys.stdout.write(ligne + "\n")
    sys.stdout.flush()


class Progres:
    def __init__(self, ecrire=_ecrire_stdout, intervalle=INTERVALLE_DEFAUT, horloge=time.monotonic):
        self._ecrire = ecrire
        self.intervalle = intervalle
        self._horloge = horloge
        self._verrou = threading.Lock()
        self._t0 = horloge()
        self._derniere = self._t0
        self._debut_etape = self._t0
        self._etape = "démarrage"
        self._compte = None
        self._arret = threading.Event()
        self._fil = None

    # ---- les lignes
    def _ligne(self, texte):
        self._ecrire(PREFIXE + texte)
        self._derniere = self._horloge()

    @staticmethod
    def _avec_compte(etape, n, total):
        return etape if n is None else "%s %d/%d" % (etape, n, total)

    def etape(self, nom, n=None, total=None):
        """Une nouvelle étape (avec son compte, s'il y en a un)."""
        with self._verrou:
            self._etape, self._compte = nom, (None if n is None else (n, total))
            self._debut_etape = self._horloge()
            self._ligne(self._avec_compte(nom, n, total))

    def avancer(self, n, total, nom=None):
        """Un pas dans l'étape en cours (ou dans l'étape `nom`, qui devient l'étape en cours)."""
        with self._verrou:
            if nom is not None and nom != self._etape:
                self._etape, self._debut_etape = nom, self._horloge()
            self._compte = (n, total)
            self._ligne(self._avec_compte(self._etape, n, total))

    def rappel(self):
        """Un `progres(étape, n, total)` pour les fonctions qui ne connaissent pas cette classe."""
        return lambda nom, n, total: self.avancer(n, total, nom)

    def battement(self):
        with self._verrou:
            now = self._horloge()
            self._ligne("%s ; en cours depuis %d s ; %d s au total" % (
                self._avec_compte(self._etape, *(self._compte or (None, None))), now - self._debut_etape, now - self._t0))

    # ---- le fil du battement
    def _boucle(self):
        while True:
            attente = self.intervalle - (self._horloge() - self._derniere)
            if attente <= 0:
                self.battement()
                continue
            if self._arret.wait(attente):
                return

    def __enter__(self):
        self._fil = threading.Thread(target=self._boucle, name="battement", daemon=True)
        self._fil.start()
        return self

    def __exit__(self, *exc):
        self._arret.set()
        self._fil.join()
        return False


class Muet(Progres):
    """Ne dit rien (`fabriquer`) : mêmes appels, aucun fil, aucune ligne."""

    def __init__(self):
        super().__init__(ecrire=lambda ligne: None, intervalle=float("inf"))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

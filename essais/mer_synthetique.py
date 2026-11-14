"""Une grille synthétique du zoom 10 pour la mer, fabriquée bande par bande (jamais en entier) : côte irrégulière, lacs,
dépressions intérieures, un couloir de 3 pixels qui relie une grande dépression à la mer, un couloir d'un pixel vers une petite, un lac dont le seul
contact avec un étang du bord de l'emprise (sans graine) est un coin en diagonale : ce contact ne relie rien en
connexité 4, donc ni l'un ni l'autre n'est de la mer.

Chaque pixel ne dépend que de ses coordonnées : la grille est la même quelle que soit la hauteur des bandes.
"""
import numpy as np


class MerSynthetique:
    def __init__(self, haut, larg, graine=7):
        self.haut, self.larg = haut, larg
        rng = np.random.default_rng(graine)
        self.sel = int(graine)
        i = np.arange(haut)
        marche = np.cumsum(rng.integers(-2, 3, haut))
        marche = marche - np.linspace(marche[0], marche[-1], haut)
        self.cote = np.clip((0.18 * larg + 0.04 * larg * np.sin(i / (haut / 9.0)) + 0.02 * larg * np.sin(i / (haut / 37.0) + 1)
                             + 5 * np.sin(i / 5.3) + marche).astype(np.int64), 3, larg - 3)
        self.marge = max(4, min(haut, larg) // 50)
        # lacs et dépressions : (ligne, colonne, rayon en lignes, rayon en colonnes)
        n = max(20, (haut * larg) // 400000)
        ri = rng.integers(2, max(4, haut // 60), n)
        rj = rng.integers(2, max(4, larg // 60), n)
        ci = rng.integers(0, haut, n)
        cj = rng.integers(int(0.30 * larg), larg, n)
        self.ellipses = [(int(a), int(b), int(c), int(d)) for a, b, c, d in zip(ci, cj, ri, rj)]
        # une grande dépression intérieure reliée à la mer par un couloir de 3 pixels
        self.grande = (int(haut * 0.50), int(larg * 0.55), max(5, haut // 30), max(8, larg // 30))
        # une petite dépression, reliée à la mer par un couloir d'un pixel
        self.petite = (int(haut * 0.35), int(larg * 0.45), 4, 6)
        # un étang au bord de l'emprise, sans graine (rectangle [ligne0, ligne1) x [col0, col1)), et un lac à graine qui ne le
        # touche que par un coin, en diagonale
        m = self.marge
        c0 = int(larg * 0.85)
        self.etang = (m - 2, m + 3, c0, c0 + 10)
        self.lac_diagonal = (m + 3, m + 8, c0 + 10, c0 + 16)

    def _hash(self, i, j, sel):
        return ((i * 73856093) ^ (j * 19349663) ^ ((self.sel + sel) * 83492791)) % 1000003

    def _peindre(self, bas, i0, e):
        ci, cj, ri, rj = e
        ia, ib = max(i0, ci - ri), min(i0 + bas.shape[0], ci + ri + 1)
        ja, jb = max(0, cj - rj), min(self.larg, cj + rj + 1)
        if ia >= ib or ja >= jb:
            return
        ii = np.arange(ia, ib)[:, None]
        jj = np.arange(ja, jb)[None, :]
        bas[ia - i0:ib - i0, ja:jb] |= ((ii - ci) / ri) ** 2 + ((jj - cj) / rj) ** 2 <= 1.0

    def _trait(self, bas, i0, ligne, j0, j1, epaisseur):
        a, b = max(i0, ligne), min(i0 + bas.shape[0], ligne + epaisseur)
        if a < b:
            bas[a - i0:b - i0, max(0, j0):min(self.larg, j1)] = True

    def bande(self, i0, i1):
        """(bas, graine) des lignes [i0, i1) : deux tableaux de booléens (i1 - i0, larg)."""
        i = np.arange(i0, i1, dtype=np.int64)[:, None]
        j = np.arange(self.larg, dtype=np.int64)[None, :]
        bas = j < self.cote[i0:i1, None]
        for e in self.ellipses:
            self._peindre(bas, i0, e)
        self._peindre(bas, i0, self.grande)
        ci, cj, ri, rj = self.grande
        self._trait(bas, i0, ci, int(self.cote[ci]), cj - rj, 3)             # le couloir de trois pixels vers la mer
        self._peindre(bas, i0, self.petite)
        ci, cj, ri, rj = self.petite
        self._trait(bas, i0, ci, int(self.cote[ci]), cj - rj, 1)             # le couloir d'un pixel
        for (r0, r1, c0, c1) in (self.etang, self.lac_diagonal):
            a, b = max(i0, r0), min(i1, r1)
            if a < b:
                bas[a - i0:b - i0, c0:c1] = True
        bas |= (self._hash(i, j, 1) % 1009) == 0                                # des pixels isolés, partout
        graine = bas & ((self._hash(i, j, 2) % 997) == 0)
        # l'étang du bord n'a aucune graine ; le lac en diagonale en a une, dans son coin
        r0, r1, c0, c1 = self.etang
        a, b = max(i0, r0), min(i1, r1)
        if a < b:
            graine[a - i0:b - i0, c0:c1] = False
        r0, _, c0, _ = self.lac_diagonal
        if i0 <= r0 < i1:
            graine[r0 - i0, c0] = True
        return bas, graine

    def dedans(self, i0, i1):
        """Les lignes [i0, i1) de l'emprise : un rectangle à marge, un coin coupé en diagonale."""
        i = np.arange(i0, i1, dtype=np.int64)[:, None]
        j = np.arange(self.larg, dtype=np.int64)[None, :]
        m = self.marge
        return (i >= m) & (i < self.haut - m) & (j >= m) & (j < self.larg - m) & ((i - m) + (j - m) > 12 * m)

    def tout(self):
        """La grille entière (essais sur de petites grilles seulement) : bas, graine, dedans."""
        bas, graine = self.bande(0, self.haut)
        return bas, graine, self.dedans(0, self.haut)

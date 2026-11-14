"""La `mer_globale` de la base (commit 918b85b), mot pour mot : l'étalon de la mer par bandes."""
import numpy as np
from scipy import ndimage


def mer_globale_base(bas, graine, dedans):
    lab, n = ndimage.label(bas)
    if n == 0:
        return np.zeros(bas.shape, dtype=bool)
    bord = dedans & ~ndimage.binary_erosion(dedans, border_value=0)
    ids_bord = np.unique(lab[bord & bas])
    ids_graine = np.unique(lab[graine & bas])
    ids = np.intersect1d(ids_bord, ids_graine)
    ids = ids[ids > 0]
    if len(ids) == 0:
        return np.zeros(bas.shape, dtype=bool)
    mer = np.isin(lab, ids)
    del lab
    return ndimage.binary_dilation(mer, structure=np.ones((3, 3), dtype=bool))

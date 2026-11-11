"""Le magasin sur disque : les entités d'OpenStreetMap retenues, géométrie entière, avec un index spatial (R-tree).

C'est un fichier SQLite dans le dossier de travail (à côté de la sortie, effacé à la fin, même sur une erreur). La
mémoire ne garde rien des entités : un bloc interroge le magasin par sa boîte, une couche à la fois.
Les coordonnées sont des mètres Mercator ; la géométrie est un WKB.
"""
import json
import os
import sqlite3

import shapely


class Magasin:
    def __init__(self, chemin):
        self.chemin = chemin
        self.db = sqlite3.connect(chemin)
        self.db.execute("PRAGMA journal_mode=OFF")
        self.db.execute("PRAGMA synchronous=OFF")
        self.db.execute("PRAGMA cache_size=-32768")           # 32 Mio de cache de pages
        self.db.execute("CREATE TABLE ent(id INTEGER PRIMARY KEY, couche TEXT NOT NULL, ident TEXT NOT NULL, "
                        "props TEXT NOT NULL, geom BLOB NOT NULL)")
        self.db.execute("CREATE UNIQUE INDEX ent_ident ON ent(couche, ident)")
        self.db.execute("CREATE VIRTUAL TABLE rt USING rtree(id, minx, maxx, miny, maxy)")
        # les chemins bruts, avant l'assemblage en lignes continues (extrémités par identifiants de nœuds)
        self.db.execute("CREATE TABLE brut(id INTEGER PRIMARY KEY, groupe TEXT NOT NULL, ident INTEGER NOT NULL, "
                        "n0 INTEGER NOT NULL, n1 INTEGER NOT NULL, props TEXT NOT NULL, geom BLOB NOT NULL)")
        self.db.execute("CREATE UNIQUE INDEX brut_ident ON brut(groupe, ident)")
        self.db.execute("CREATE TABLE zone(id INTEGER PRIMARY KEY, ident INTEGER NOT NULL, props TEXT NOT NULL, "
                        "geom BLOB NOT NULL)")
        self.db.execute("CREATE UNIQUE INDEX zone_ident ON zone(ident)")
        self.db.execute("CREATE VIRTUAL TABLE rtz USING rtree(id, minx, maxx, miny, maxy)")
        self._n = 0

    # ------------------------------------------------------------------ écriture
    def ajouter(self, couche, ident, props, geom):
        """Le premier arrivé garde la place : à doublon (couche, ident), l'ajout est ignoré. Rend True si ajouté."""
        self._n += 1
        cur = self.db.execute("INSERT OR IGNORE INTO ent(id, couche, ident, props, geom) VALUES (?,?,?,?,?)",
                              (self._n, couche, ident, json.dumps(props, sort_keys=True, ensure_ascii=False),
                               shapely.to_wkb(geom)))
        if cur.rowcount == 0:
            return False
        x0, y0, x1, y1 = geom.bounds
        self.db.execute("INSERT INTO rt VALUES (?,?,?,?,?)", (self._n, x0, x1, y0, y1))
        return True

    def ajouter_brut(self, groupe, ident, n0, n1, props, geom):
        self._n += 1
        self.db.execute("INSERT OR IGNORE INTO brut(id, groupe, ident, n0, n1, props, geom) VALUES (?,?,?,?,?,?,?)",
                        (self._n, groupe, ident, n0, n1, json.dumps(props, sort_keys=True, ensure_ascii=False),
                         shapely.to_wkb(geom)))

    def ajouter_zone(self, ident, props, geom):
        self._n += 1
        cur = self.db.execute("INSERT OR IGNORE INTO zone(id, ident, props, geom) VALUES (?,?,?,?)",
                              (self._n, ident, json.dumps(props, sort_keys=True), shapely.to_wkb(geom)))
        if cur.rowcount:
            x0, y0, x1, y1 = geom.bounds
            self.db.execute("INSERT INTO rtz VALUES (?,?,?,?,?)", (self._n, x0, x1, y0, y1))

    def valider(self):
        self.db.commit()

    # ------------------------------------------------------------------ lecture
    @staticmethod
    def _ligne(r):
        return r[0], json.loads(r[1]), shapely.from_wkb(r[2])

    def entites(self, couche, boite=None, lot=500):
        """(ident, props, géométrie) de la couche, triés par identifiant ; par la boîte (xmin, ymin, xmax, ymax) si donnée.
        Un générateur : rien n'est gardé."""
        if boite is None:
            rows = self.db.execute("SELECT ident, props, geom FROM ent WHERE couche=? ORDER BY ident", (couche,))
        else:
            rows = self.db.execute(
                "SELECT e.ident, e.props, e.geom FROM rt JOIN ent e ON e.id = rt.id WHERE e.couche=? "
                "AND rt.minx<=? AND rt.maxx>=? AND rt.miny<=? AND rt.maxy>=? ORDER BY e.ident",
                (couche, boite[2], boite[0], boite[3], boite[1]))
        while True:
            paquet = rows.fetchmany(lot)
            if not paquet:
                return
            for r in paquet:
                yield self._ligne(r)

    def compte(self, couche):
        return self.db.execute("SELECT COUNT(*) FROM ent WHERE couche=?", (couche,)).fetchone()[0]

    def brutes(self, groupe):
        """Les chemins bruts d'un groupe : (ident, n0, n1, props, géométrie), triés par identifiant."""
        for r in self.db.execute("SELECT ident, n0, n1, props, geom FROM brut WHERE groupe=? ORDER BY ident", (groupe,)):
            yield r[0], r[1], r[2], json.loads(r[3]), shapely.from_wkb(r[4])

    def groupes_bruts(self):
        return [r[0] for r in self.db.execute("SELECT DISTINCT groupe FROM brut ORDER BY groupe")]

    def zones(self):
        for r in self.db.execute("SELECT ident, geom FROM zone ORDER BY ident"):
            yield r[0], shapely.from_wkb(r[1])

    def zones_boite(self, boite):
        rows = self.db.execute(
            "SELECT z.ident, z.geom FROM rtz JOIN zone z ON z.id = rtz.id WHERE rtz.minx<=? AND rtz.maxx>=? "
            "AND rtz.miny<=? AND rtz.maxy>=? ORDER BY z.ident", (boite[2], boite[0], boite[3], boite[1]))
        for r in rows:
            yield r[0], shapely.from_wkb(r[1])

    def effacer_brutes(self):
        self.db.execute("DELETE FROM brut")
        self.db.execute("DELETE FROM zone")
        self.db.execute("DELETE FROM rtz")
        self.db.commit()

    def fermer(self):
        try:
            self.db.close()
        except sqlite3.Error:
            pass


class Fragments:
    """Les morceaux de tuiles des zooms de survol (au-dessous du zoom pivot), rangés sur disque jusqu'à l'assemblage."""

    def __init__(self, chemin):
        self.db = sqlite3.connect(chemin)
        self.db.execute("PRAGMA journal_mode=OFF")
        self.db.execute("PRAGMA synchronous=OFF")
        self.db.execute("PRAGMA cache_size=-16384")
        self.db.execute("CREATE TABLE frag(z INTEGER, x INTEGER, y INTEGER, rang INTEGER, cle TEXT, props TEXT, geom BLOB)")
        self._tampon = []

    def ajouter(self, z, x, y, rang, cle, geom, props):
        self._tampon.append((z, x, y, rang, cle, json.dumps(props, sort_keys=True, ensure_ascii=False),
                             shapely.to_wkb(geom)))
        if len(self._tampon) >= 2000:
            self.vider()

    def vider(self):
        if self._tampon:
            self.db.executemany("INSERT INTO frag VALUES (?,?,?,?,?,?,?)", self._tampon)
            self._tampon = []

    def indexer(self):
        self.vider()
        self.db.execute("CREATE INDEX frag_tuile ON frag(z, x, y, rang, cle)")
        self.db.commit()

    def tuiles(self):
        return [tuple(r) for r in self.db.execute("SELECT DISTINCT z, x, y FROM frag ORDER BY z, x, y")]

    def de_la_tuile(self, z, x, y):
        """{rang: [(cle, géométrie, props)]} de la tuile, dans l'ordre des clés."""
        out = {}
        for rang, cle, props, wkb in self.db.execute(
                "SELECT rang, cle, props, geom FROM frag WHERE z=? AND x=? AND y=? ORDER BY rang, cle", (z, x, y)):
            out.setdefault(rang, []).append((cle, shapely.from_wkb(wkb), json.loads(props)))
        return out

    def fermer(self):
        try:
            self.db.close()
        except sqlite3.Error:
            pass


def taille_dossier(dossier):
    n = 0
    for r, _, fs in os.walk(dossier):
        for f in fs:
            try:
                n += os.path.getsize(os.path.join(r, f))
            except OSError:
                pass
    return n

"""Tests de menage_branches.py (lot co-menage-branches).

Tout se joue sur de faux dépôts : un dépôt nu « origin » et des clones dans un dossier temporaire, les dates étant
fixées par GIT_AUTHOR_DATE / GIT_COMMITTER_DATE et l'heure de référence par --date. Aucun vrai dépôt n'est lu ni écrit.

Lancer (depuis la racine du dépôt) : python3 -B -m unittest discover -s <dossier de ce fichier> -v
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parent.parent / "menage_branches.py"
REF = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
DATE = "2026-11-15 12:00"


def il_y_a(jours=0, heures=0, minutes=0):
    return REF - timedelta(days=jours, hours=heures, minutes=minutes)


def iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%S+0000")


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        env = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
               "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
               "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        for var in ("GITHUB_STEP_SUMMARY", "GITHUB_TOKEN", "GITHUB_REPOSITORY", "GITHUB_API_URL"):
            os.environ.pop(var, None)
        self.origin = self.tmp / "origin.git"
        self.git("init", "-q", "--bare", "-b", "main", str(self.origin), cwd=self.tmp)
        self.work = self.tmp / "work"
        self.git("clone", "-q", str(self.origin), str(self.work), cwd=self.tmp)
        self.git("checkout", "-q", "-b", "main", cwd=self.work)
        self.commit("base.txt", "base\n", il_y_a(30))
        self.git("push", "-q", "origin", "main", cwd=self.work)

    # --- outils ---
    def git(self, *args, cwd=None, date=None, check=True):
        env = dict(os.environ)
        if date is not None:
            env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = iso(date)
        r = subprocess.run(["git", *args], cwd=cwd or self.work, env=env, capture_output=True, text=True)
        if check and r.returncode != 0:
            raise AssertionError(f"git {args} : {r.stderr}")
        return r

    def commit(self, fichier, contenu, date, message=None):
        (self.work / fichier).parent.mkdir(parents=True, exist_ok=True)
        (self.work / fichier).write_text(contenu, encoding="utf-8")
        self.git("add", "-A", date=date)
        self.git("commit", "-q", "-m", message or f"commit {fichier}", date=date)
        return self.git("rev-parse", "HEAD").stdout.strip()

    def branche_ff(self, nom, date):
        """Une branche et main sur le même nouveau commit (avance rapide)."""
        self.git("checkout", "-q", "main")
        self.commit(f"ff_{nom.replace('/', '_')}.txt", nom, date)
        self.git("push", "-q", "origin", "main", f"main:refs/heads/{nom}")

    def branche_hors_main(self, nom, date, n=1):
        """Une branche de n commits partis de main ; rend les sha. Main n'est pas touchée."""
        self.git("checkout", "-q", "-b", "tmp", "main")
        shas = [self.commit(f"b_{nom.replace('/', '_')}_{i}.txt", f"{nom} {i}\n", date) for i in range(n)]
        self.git("push", "-q", "origin", f"tmp:refs/heads/{nom}")
        self.git("checkout", "-q", "main")
        self.git("branch", "-q", "-D", "tmp")
        return shas

    def rejouer(self, shas, date):
        """Rejoue (cherry-pick) des commits sur main, à une autre date."""
        self.git("checkout", "-q", "main")
        for s in shas:
            self.git("cherry-pick", s, date=date)
        self.git("push", "-q", "origin", "main")

    def tetes(self):
        sortie = self.git("ls-remote", "--heads", str(self.origin)).stdout
        return {l.split("\t")[1][len("refs/heads/"):] for l in sortie.splitlines()}

    def lancer(self, *args, protegees="", date=DATE, extra_env=None, clone_args=()):
        clone = self.tmp / "clone"
        shutil.rmtree(clone, ignore_errors=True)
        self.git("clone", "-q", *clone_args, ("file://" if clone_args else "") + str(self.origin), str(clone),
                 cwd=self.tmp)
        cmd = [sys.executable, "-B", str(SCRIPT), "--depot", str(clone), "--date", date]
        if protegees is not None:
            pf = self.tmp / "protegees.txt"
            pf.write_text(protegees, encoding="utf-8")
            cmd += ["--protegees-fichier", str(pf)]
        env = dict(os.environ)
        env.update(extra_env or {})
        r = subprocess.run(cmd + list(args), env=env, capture_output=True, text=True)
        self.sortie = r
        return r

    def decisions(self, r=None):
        r = r or self.sortie
        res = {}
        for l in r.stdout.splitlines():
            if l.startswith("| ") and not l.startswith("| branche") and not l.startswith("|---"):
                cols = [c.strip() for c in l.strip("|").split(" | ")]
                res[cols[0]] = (cols[2], cols[3])
        return res


class TestRegle(Fixture):
    def test_avance_rapide_ancienne_supprimee(self):
        self.branche_ff("lot/ff", il_y_a(10))
        r = self.lancer()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("lot/ff", self.tetes())
        self.assertEqual(self.decisions()["lot/ff"][0], "supprimée")

    def test_seuil_des_7_jours(self):
        self.branche_ff("lot/six", il_y_a(6, 23))
        self.branche_ff("lot/sept", il_y_a(7))
        self.branche_ff("lot/sept-et-une-minute", il_y_a(7, 0, 1))
        r = self.lancer()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.tetes(), {"main", "lot/six"})
        d = self.decisions()
        self.assertEqual(d["lot/six"][0], "gardée")
        self.assertIn("trop récente", d["lot/six"][1])
        self.assertEqual(d["lot/sept"][0], "supprimée")

    def test_delai_reglable(self):
        self.branche_ff("lot/dix", il_y_a(10))
        self.lancer("--delai-jours", "14")
        self.assertIn("lot/dix", self.tetes())
        self.lancer("--delai-jours", "10")
        self.assertNotIn("lot/dix", self.tetes())

    def test_fusion_par_commit_de_fusion(self):
        self.git("checkout", "-q", "-b", "tmp", "main")
        self.commit("m.txt", "m\n", il_y_a(12))
        self.git("push", "-q", "origin", "tmp:refs/heads/lot/fusion")
        self.git("checkout", "-q", "main")
        self.commit("autre.txt", "autre\n", il_y_a(11))
        self.git("merge", "-q", "--no-ff", "-m", "fusion", "tmp", date=il_y_a(1))
        self.git("push", "-q", "origin", "main")
        r = self.lancer()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("lot/fusion", self.tetes())

    def test_rejeu_cherry_pick_supprimee(self):
        shas = self.branche_hors_main("lot/rejeu", il_y_a(12), n=2)
        self.commit("entre.txt", "e\n", il_y_a(11))
        self.git("push", "-q", "origin", "main")
        self.rejouer(shas, il_y_a(2))
        r = self.lancer()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("lot/rejeu", self.tetes())
        self.assertIn("main", self.tetes())

    def test_rejeu_avec_conflit_resolu_autrement_gardee(self):
        self.commit("f.txt", "a\n", il_y_a(20))
        self.git("push", "-q", "origin", "main")
        self.git("checkout", "-q", "-b", "tmp", "main")
        self.commit("f.txt", "b\n", il_y_a(12))
        self.git("push", "-q", "origin", "tmp:refs/heads/lot/conflit")
        self.git("checkout", "-q", "main")
        self.git("branch", "-q", "-D", "tmp")
        self.commit("f.txt", "c\n", il_y_a(2), message="résolu autrement")
        self.git("push", "-q", "origin", "main")
        r = self.lancer()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("lot/conflit", self.tetes())
        d = self.decisions()["lot/conflit"]
        self.assertEqual(d[0], "gardée")
        self.assertIn("non fusionnée (1 commit(s)", d[1])

    def test_un_commit_de_plus_hors_de_main_gardee(self):
        shas = self.branche_hors_main("lot/reste", il_y_a(12), n=2)
        self.rejouer(shas[:1], il_y_a(2))
        r = self.lancer()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("lot/reste", self.tetes())
        self.assertIn("non fusionnée (1 commit(s)", self.decisions()["lot/reste"][1])

    def test_non_fusionnee_recente_dite_non_fusionnee(self):
        self.branche_hors_main("lot/neuve", il_y_a(0, 1), n=3)
        self.lancer()
        self.assertIn("non fusionnée (3 commit(s)", self.decisions()["lot/neuve"][1])

    def test_branche_finie_mais_recente_gardee(self):
        shas = self.branche_hors_main("lot/jeune", il_y_a(3))
        self.rejouer(shas, il_y_a(1))
        self.lancer()
        self.assertIn("lot/jeune", self.tetes())
        self.assertIn("trop récente", self.decisions()["lot/jeune"][1])

    def test_release_avec_tag_supprimee_sans_tag_gardee(self):
        self.branche_hors_main("release/v1.0", il_y_a(9))
        self.branche_hors_main("release/v2.0", il_y_a(9))
        self.git("tag", "v1.0", "main")
        self.git("push", "-q", "origin", "v1.0")
        r = self.lancer()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.tetes(), {"main", "release/v2.0"})
        self.assertIn("tag « v2.0 » absent", self.decisions()["release/v2.0"][1])

    def test_release_avec_tag_annote(self):
        self.branche_hors_main("release/v3.0", il_y_a(9))
        self.git("tag", "-a", "-m", "annoté", "v3.0", "main", date=il_y_a(9))
        self.git("push", "-q", "origin", "v3.0")
        self.lancer()
        self.assertNotIn("release/v3.0", self.tetes())

    def test_release_avec_tag_recente_gardee(self):
        self.branche_hors_main("release/v4.0", il_y_a(2))
        self.git("tag", "v4.0", "main")
        self.git("push", "-q", "origin", "v4.0")
        self.lancer()
        self.assertIn("release/v4.0", self.tetes())

    def test_release_essai(self):
        self.branche_hors_main("release/essai-1", il_y_a(9))
        self.branche_hors_main("release/essai-2", il_y_a(1))
        self.lancer()
        self.assertEqual(self.tetes(), {"main", "release/essai-2"})

    def test_noms_a_plusieurs_barres(self):
        shas = self.branche_hors_main("lot/a/b/c", il_y_a(12))
        self.branche_hors_main("prod/x/y", il_y_a(12), n=1)
        self.branche_hors_main("release/sous/9.9", il_y_a(12))
        self.git("tag", "sous/9.9", "main")
        self.git("push", "-q", "origin", "sous/9.9")
        self.rejouer(shas, il_y_a(2))
        r = self.lancer()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.tetes(), {"main", "prod/x/y"})

    def test_main_jamais(self):
        # main est ancienne et « finie » : jamais supprimée, même avec un délai de 1 jour
        r = self.lancer("--delai-jours", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.tetes(), {"main"})
        self.assertEqual(self.decisions()["main"], ("gardée", "main"))

    def test_branche_protegee_jamais(self):
        self.branche_ff("lot/protegee", il_y_a(20))
        self.branche_ff("lot/libre", il_y_a(20))
        self.lancer(protegees="# commentaire\nlot/protegee\n")
        self.assertEqual(self.tetes(), {"main", "lot/protegee"})
        self.assertEqual(self.decisions()["lot/protegee"], ("gardée", "branche protégée"))

    def test_protegee_meme_release_essai(self):
        self.branche_hors_main("release/essai-9", il_y_a(20))
        self.lancer(protegees="release/essai-9\n")
        self.assertIn("release/essai-9", self.tetes())


class TestDefautAutre(Fixture):
    def setUp(self):
        super().setUp()
        self.git("push", "-q", "origin", "main:refs/heads/develop")
        self.git("--git-dir", str(self.origin), "symbolic-ref", "HEAD", "refs/heads/develop", cwd=self.tmp)

    def test_defaut_et_main_jamais_supprimees(self):
        r = self.lancer("--delai-jours", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.tetes(), {"main", "develop"})
        d = self.decisions()
        self.assertEqual(d["develop"], ("gardée", "branche par défaut"))
        self.assertEqual(d["main"], ("gardée", "main"))

    def test_finie_envers_main_et_defaut(self):
        shas = self.branche_hors_main("lot/seulement-main", il_y_a(12))
        self.branche_hors_main("lot/vide", il_y_a(12), n=0)
        self.rejouer(shas, il_y_a(2))  # main a le rejeu, develop non
        r = self.lancer()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("lot/seulement-main", self.tetes())
        self.assertIn("non fusionnée", self.decisions()["lot/seulement-main"][1])


class TestEssaiEtPannes(Fixture):
    def test_essai_ne_supprime_rien(self):
        self.branche_ff("lot/ff", il_y_a(10))
        self.branche_hors_main("release/essai-1", il_y_a(10))
        avant = self.tetes()
        r = self.lancer("--essai")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.tetes(), avant)
        self.assertTrue(r.stdout.startswith("MODE ESSAI : rien n'est supprimé."))
        d = self.decisions()
        self.assertEqual(d["lot/ff"][0], "à supprimer")
        self.assertEqual(d["release/essai-1"][0], "à supprimer")

    def test_rien_a_supprimer_code_zero(self):
        r = self.lancer()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("0 supprimée(s)", r.stdout)

    def test_resume_du_job(self):
        self.branche_ff("lot/ff", il_y_a(10))
        resume = self.tmp / "resume.md"
        self.lancer(extra_env={"GITHUB_STEP_SUMMARY": str(resume)})
        txt = resume.read_text(encoding="utf-8")
        self.assertIn("| branche | âge | décision | raison |", txt)
        self.assertIn("| lot/ff | 10 j 0 h | supprimée |", txt)

    def test_clone_partiel_est_une_panne(self):
        self.branche_ff("lot/ff", il_y_a(10))
        r = self.lancer(clone_args=("--depth", "1"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("PANNE", r.stderr)
        self.assertIn("lot/ff", self.tetes())

    def test_depot_illisible_est_une_panne(self):
        r = subprocess.run([sys.executable, "-B", str(SCRIPT), "--depot", str(self.tmp / "inexistant"),
                            "--protegees-fichier", os.devnull, "--date", DATE], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)

    def test_bail_refuse_si_la_branche_a_bouge(self):
        sys.path.insert(0, str(SCRIPT.parent))
        try:
            import menage_branches
        finally:
            sys.path.pop(0)
        self.branche_ff("lot/bouge", il_y_a(10))
        vieux = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("checkout", "-q", "-b", "tmp", "main")
        self.commit("neuf.txt", "neuf\n", il_y_a(0, 1))
        self.git("push", "-q", "origin", "tmp:refs/heads/lot/bouge")  # fast-forward de la branche
        refus = menage_branches.supprimer(str(self.work), "lot/bouge", vieux)
        self.assertIsNotNone(refus)
        self.assertIn("lot/bouge", self.tetes())
        self.assertIsNone(menage_branches.supprimer(str(self.work), "lot/bouge",
                                                    self.git("rev-parse", "HEAD").stdout.strip()))
        self.assertNotIn("lot/bouge", self.tetes())


class _Api(BaseHTTPRequestHandler):
    reponse = (200, [])
    vus = []

    def do_GET(self):
        type(self).vus.append((self.path, self.headers.get("Authorization")))
        code, corps = type(self).reponse
        data = json.dumps(corps).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


class TestApiProtegees(Fixture):
    def serveur(self, code, corps):
        _Api.reponse, _Api.vus = (code, corps), []
        srv = HTTPServer(("127.0.0.1", 0), _Api)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return f"http://127.0.0.1:{srv.server_address[1]}"

    def env(self, url):
        return {"GITHUB_API_URL": url, "GITHUB_REPOSITORY": "proprio/depot", "GITHUB_TOKEN": "jeton-test"}

    def test_api_ok_protege(self):
        self.branche_ff("lot/protegee", il_y_a(20))
        self.branche_ff("lot/libre", il_y_a(20))
        url = self.serveur(200, [{"name": "lot/protegee"}])
        r = self.lancer(protegees=None, extra_env=self.env(url))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.tetes(), {"main", "lot/protegee"})
        chemin, auth = _Api.vus[0]
        self.assertTrue(chemin.startswith("/repos/proprio/depot/branches?protected=true"))
        self.assertEqual(auth, "Bearer jeton-test")

    def test_api_en_panne_rien_supprime(self):
        self.branche_ff("lot/ff", il_y_a(20))
        r = self.lancer(protegees=None, extra_env=self.env("http://127.0.0.1:1"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("PANNE", r.stderr)
        self.assertIn("lot/ff", self.tetes())

    def test_api_erreur_500_rien_supprime(self):
        self.branche_ff("lot/ff", il_y_a(20))
        url = self.serveur(500, {"message": "boum"})
        r = self.lancer(protegees=None, extra_env=self.env(url))
        self.assertEqual(r.returncode, 2)
        self.assertIn("lot/ff", self.tetes())

    def test_api_reponse_inattendue_rien_supprime(self):
        self.branche_ff("lot/ff", il_y_a(20))
        url = self.serveur(200, {"message": "pas une liste"})
        r = self.lancer(protegees=None, extra_env=self.env(url))
        self.assertEqual(r.returncode, 2)
        self.assertIn("lot/ff", self.tetes())

    def test_essai_sans_api_ni_fichier_est_une_panne(self):
        # même en essai : sans liste des protégées, pas de décision
        r = self.lancer("--essai", protegees=None, extra_env=self.env("http://127.0.0.1:1"))
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()

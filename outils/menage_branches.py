#!/usr/bin/env python3
"""Le ménage des branches, en passage planifié — un script, sans session et sans jeton de modèle.

Chaque jour, une action GitHub lance ce script sur un clone complet du dépôt : il supprime les branches FINIES
depuis au moins 7 jours, et jamais une autre. Il liste toutes les décisions (supprimée, gardée, avec la raison).

    python3 -B menage_branches.py [--essai] [--delai-jours N] [--date "AAAA-MM-JJ HH:MM"] [--depot <dossier>]
                                  [--protegees-fichier <fichier>]

Une branche est supprimée si et seulement si :
  1. ce n'est ni « main », ni la branche par défaut du dépôt, ni une branche protégée ;
  2. son dernier commit a N jours au moins (date du committer ; N = 7 par défaut, « --delai-jours ») ;
  3. et elle est finie, selon son nom :
       - « release/essai-* » : toujours (un essai ne crée rien) ;
       - « release/<TAG> » : le tag <TAG> existe (la release est publiée) ;
       - toute autre branche (« lot/* », « prod/* »…) : tout son contenu est dans « main », c'est-à-dire que chaque
         commit de la branche qui n'est pas dans « main » y a son équivalent par patch-id (« git cherry » ne rend
         aucune ligne « + »). Cela couvre l'avance rapide, la fusion par commit de fusion et le rejeu des commits.
         Une branche sans aucun commit hors de « main » est finie. Si la branche par défaut n'est pas « main », la
         branche doit être finie envers les deux.
Tout le reste est gardé et listé : non fusionnée (avec le nombre de commits « + »), tag absent, trop récente (avec
son âge). Le doute garde la branche : un rejeu qui a résolu un conflit (patch-id différent) laisse la branche,
listée « non fusionnée ». Une branche non finie est dite telle même si elle est aussi récente.

Les options :
  --essai              mêmes décisions, rien n'est supprimé ; la sortie le dit en tête.
  --delai-jours N      l'âge minimal du dernier commit, en jours entiers (7 par défaut, 1 au moins).
  --date "AAAA-MM-JJ HH:MM"   l'heure de référence (UTC ; une date ISO avec fuseau est acceptée) ; pour les tests.
                       Sans elle, l'heure courante.
  --depot <dossier>    le dossier du clone (le dossier courant par défaut). Le clone doit être complet (pas de
                       --depth) : un clone partiel est une panne, « git cherry » y serait faux. Le script y lance
                       « git fetch » sur « origin » pour avoir toutes les branches.
  --protegees-fichier <fichier>  la liste des branches protégées (une par ligne, « # » pour un commentaire), lue
                       à la place de l'API ; pour les tests et les essais sans jeton. Un fichier vide : aucune.

Sans ce fichier, les branches protégées viennent de l'API GitHub : GET /repos/{propriétaire}/{dépôt}/branches?
protected=true (jeton « GITHUB_TOKEN », dépôt « GITHUB_REPOSITORY » ou déduit de l'URL de « origin », serveur
« GITHUB_API_URL »). Sans réponse de l'API, RIEN n'est supprimé : c'est une panne.

La sortie : sur la sortie standard et dans le résumé du job ($GITHUB_STEP_SUMMARY), un tableau (branche, âge,
décision, raison). Le code de sortie : 0 si le passage s'est fait (même sans rien supprimer) ; 2 en panne seulement
(dépôt illisible ou partiel, API muette, suppression refusée par GitHub) : l'e-mail d'échec de GitHub veut dire
« panne ». La suppression se fait par « git push origin --delete », avec un bail sur le commit lu (si la branche a
reçu un commit entre-temps, GitHub refuse : la branche reste).

Bibliothèque standard seule, Python 3.11 au moins.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

DELAI_JOURS = 7
CODE_PANNE = 2


class Panne(Exception):
    """Une panne : le passage ne peut pas se faire sûrement, rien (de plus) n'est supprimé."""


def git(depot, *args):
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        r = subprocess.run(["git", *args], cwd=depot, env=env, capture_output=True, text=True)
    except OSError as e:
        raise Panne(f"git est introuvable ou ne se lance pas : {e}")
    if r.returncode != 0:
        raise Panne(f"git {' '.join(args)} a échoué (code {r.returncode}) : {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout


def lire_distant(depot):
    """(têtes {nom: sha}, tags {nom}, branche par défaut ou None) d'après « git ls-remote » sur origin."""
    sortie = git(depot, "ls-remote", "--symref", "origin")
    tetes, tags, defaut = {}, set(), None
    for ligne in sortie.splitlines():
        if not ligne.strip():
            continue
        if ligne.startswith("ref:"):
            m = re.match(r"ref:\s+refs/heads/(\S.*?)\s+HEAD$", ligne)
            if m:
                defaut = m.group(1)
            continue
        sha, _, ref = ligne.partition("\t")
        if ref.startswith("refs/heads/"):
            tetes[ref[len("refs/heads/"):]] = sha
        elif ref.startswith("refs/tags/"):
            nom = ref[len("refs/tags/"):]
            tags.add(nom[:-3] if nom.endswith("^{}") else nom)
    return tetes, tags, defaut


def depot_api(depot):
    d = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if d:
        return d
    url = git(depot, "remote", "get-url", "origin").strip()
    m = re.search(r"github\.com[:/]+([^/]+/[^/]+?)(?:\.git)?/?$", url)
    if not m:
        raise Panne(f"le dépôt GitHub ne se déduit pas de l'URL de origin ({url}) ; poser GITHUB_REPOSITORY")
    return m.group(1)


def protegees_api(depot):
    base = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    depot_gh = depot_api(depot)
    jeton = os.environ.get("GITHUB_TOKEN", "").strip()
    noms, page = set(), 1
    while True:
        url = f"{base}/repos/{depot_gh}/branches?protected=true&per_page=100&page={page}"
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                                   "User-Agent": "menage-branches"})
        if jeton:
            req.add_header("Authorization", f"Bearer {jeton}")
        try:
            with urllib.request.urlopen(req, timeout=30) as rep:
                donnees = json.loads(rep.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise Panne(f"l'API des branches protégées ne répond pas ({url}) : {e}. Rien n'est supprimé.")
        if not isinstance(donnees, list):
            raise Panne(f"l'API des branches protégées a rendu une réponse inattendue ({url}). Rien n'est supprimé.")
        for b in donnees:
            if not isinstance(b, dict) or "name" not in b:
                raise Panne("l'API des branches protégées a rendu une entrée inattendue. Rien n'est supprimé.")
            noms.add(b["name"])
        if len(donnees) < 100:
            return noms
        page += 1
        if page > 50:
            raise Panne("l'API des branches protégées pagine sans fin. Rien n'est supprimé.")


def protegees_fichier(chemin):
    try:
        with open(chemin, encoding="utf-8") as f:
            lignes = f.read().splitlines()
    except OSError as e:
        raise Panne(f"le fichier des branches protégées ne se lit pas : {e}")
    return {l.strip() for l in lignes if l.strip() and not l.strip().startswith("#")}


def parse_date(texte):
    if texte is None:
        return datetime.now(timezone.utc)
    t = texte.strip()
    if re.match(r"^\d{4}-\d\d-\d\d \d", t):
        t = t.replace(" ", "T", 1)
    try:
        d = datetime.fromisoformat(t)
    except ValueError:
        raise SystemExit(f"--date illisible : {texte!r} (attendu « AAAA-MM-JJ HH:MM »)")
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)


def format_age(secondes):
    secondes = max(0, int(secondes))
    j, reste = divmod(secondes, 86400)
    return f"{j} j {reste // 3600} h"


def commits_plus(depot, base_sha, sha):
    """Nombre de commits de sha sans équivalent (patch-id) dans base."""
    sortie = git(depot, "cherry", base_sha, sha)
    return sum(1 for l in sortie.splitlines() if l.startswith("+"))


def supprimer(depot, nom, sha):
    """Supprime refs/heads/<nom> sur origin, avec bail sur sha. Rend None si fait, sinon le message du refus."""
    r = subprocess.run(["git", "push", "origin", f"--force-with-lease=refs/heads/{nom}:{sha}",
                        "--delete", f"refs/heads/{nom}"], cwd=depot, capture_output=True, text=True,
                       env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    if r.returncode == 0:
        return None
    lignes = (r.stderr.strip() or r.stdout.strip()).splitlines()
    return lignes[-1] if lignes else "sans message"


def passage(depot, essai, delai_jours, maintenant, fichier_protegees):
    if git(depot, "rev-parse", "--is-shallow-repository").strip() == "true":
        raise Panne("le clone est partiel (--depth) : « git cherry » y serait faux. Cloner en entier (fetch-depth: 0).")
    git(depot, "fetch", "--quiet", "--no-tags", "--prune", "origin", "+refs/heads/*:refs/remotes/origin/*")
    tetes, tags, defaut = lire_distant(depot)
    if defaut is None:
        raise Panne("la branche par défaut du dépôt ne se lit pas (git ls-remote --symref).")
    if defaut not in tetes:
        raise Panne(f"la branche par défaut « {defaut} » n'est pas dans les têtes de origin.")
    protegees = protegees_fichier(fichier_protegees) if fichier_protegees is not None else protegees_api(depot)

    bases = {n: tetes[n] for n in {"main", defaut} if n in tetes}
    decisions = []  # (branche, age_txt, decision, raison, a_supprimer)
    for nom in sorted(tetes):
        sha = tetes[nom]
        git(depot, "cat-file", "-e", f"{sha}^{{commit}}")
        ct = int(git(depot, "log", "-1", "--format=%ct", sha).strip())
        age = maintenant.timestamp() - ct
        age_txt = format_age(age)
        if nom == "main":
            decisions.append((nom, age_txt, "gardée", "main", False)); continue
        if nom == defaut:
            decisions.append((nom, age_txt, "gardée", "branche par défaut", False)); continue
        if nom in protegees:
            decisions.append((nom, age_txt, "gardée", "branche protégée", False)); continue
        if nom.startswith("release/essai-"):
            pourquoi = "essai de release (ne crée rien)"
        elif nom.startswith("release/"):
            tag = nom[len("release/"):]
            if not tag or tag not in tags:
                decisions.append((nom, age_txt, "gardée", f"tag « {tag} » absent", False)); continue
            pourquoi = f"tag {tag} présent (release publiée)"
        else:
            plus = {b: commits_plus(depot, s, sha) for b, s in bases.items()}
            b = max(plus, key=plus.get)
            if plus[b] > 0:
                decisions.append((nom, age_txt, "gardée",
                                  f"non fusionnée ({plus[b]} commit(s) « + » hors de {b})", False)); continue
            pourquoi = "tout son contenu est dans main"
        if age < delai_jours * 86400:
            decisions.append((nom, age_txt, "gardée",
                              f"trop récente ({age_txt} < {delai_jours} j) ; finie : {pourquoi}", False)); continue
        decisions.append((nom, age_txt, "à supprimer" if essai else "supprimée", pourquoi, True))

    echecs, resultat = [], []
    for nom, age_txt, decision, raison, a_supprimer in decisions:
        if a_supprimer and not essai:
            refus = supprimer(depot, nom, tetes[nom])
            if refus is not None:
                decision, raison = "ÉCHEC de suppression", f"{raison} — refusée : {refus}"
                echecs.append(nom)
        resultat.append((nom, age_txt, decision, raison))
    return resultat, echecs, defaut


def rendre(resultat, essai, delai_jours, maintenant, defaut, echecs):
    n_supp = sum(1 for r in resultat if r[2] in ("supprimée", "à supprimer"))
    en_tete = [
        "MODE ESSAI : rien n'est supprimé." if essai else "Mode réel : les branches « supprimée » ont été supprimées.",
        f"Référence : {maintenant.strftime('%Y-%m-%d %H:%M')} UTC · délai : {delai_jours} j · branche par défaut : {defaut}",
        f"{len(resultat)} branche(s) · {n_supp} {'à supprimer' if essai else 'supprimée(s)'} · "
        f"{len(resultat) - n_supp - len(echecs)} gardée(s)" + (f" · {len(echecs)} échec(s)" if echecs else ""),
    ]
    if echecs:
        en_tete.append("PANNE : suppression refusée pour " + ", ".join(echecs))
    tableau = ["| branche | âge | décision | raison |", "|---|---|---|---|"]
    for ligne in resultat:
        tableau.append("| " + " | ".join(c.replace("|", "\\|") for c in ligne) + " |")
    return en_tete, tableau


def ecrire_resume(texte):
    chemin = os.environ.get("GITHUB_STEP_SUMMARY")
    if chemin:
        try:
            with open(chemin, "a", encoding="utf-8") as f:
                f.write(texte + "\n")
        except OSError:
            pass


def main(argv=None):
    p = argparse.ArgumentParser(description="Ménage des branches finies depuis N jours.")
    p.add_argument("--essai", action="store_true")
    p.add_argument("--delai-jours", type=int, default=DELAI_JOURS)
    p.add_argument("--date")
    p.add_argument("--depot", default=".")
    p.add_argument("--protegees-fichier")
    a = p.parse_args(argv)
    if a.delai_jours < 1:
        p.error("--delai-jours doit être au moins 1")
    maintenant = parse_date(a.date)
    try:
        resultat, echecs, defaut = passage(a.depot, a.essai, a.delai_jours, maintenant, a.protegees_fichier)
    except Panne as e:
        msg = f"PANNE : {e}"
        print(msg, file=sys.stderr)
        ecrire_resume("### Ménage des branches\n\n**" + msg + "**")
        return CODE_PANNE
    en_tete, tableau = rendre(resultat, a.essai, a.delai_jours, maintenant, defaut, echecs)
    print("\n".join(en_tete) + "\n\n" + "\n".join(tableau))
    ecrire_resume("### Ménage des branches\n\n" + "\n".join(l + "  " for l in en_tete) + "\n\n" + "\n".join(tableau))
    return CODE_PANNE if echecs else 0


if __name__ == "__main__":
    sys.exit(main())

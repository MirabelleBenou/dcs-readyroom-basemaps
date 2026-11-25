"""Publie une release GitHub a partir d'une branche release/** (bibliotheque standard seulement ; lance par
.github/workflows/release.yml).

  python3 outils/release.py       lit GITHUB_REPOSITORY, GITHUB_REF_NAME, GITHUB_TOKEN ; code 0 si tout va bien, 1 sinon

Branche release/BM-V<x.y.z> : publication. Branche release/essai-<nom> : les memes controles, rien n'est cree.
Un fichier trop gros pour un push arrive en morceaux <nom>.part001, .part002... : ils sont joints hors du depot, le fichier joint est controle puis televerse.
Une release peut n'avoir aucun fichier (assets: []).
Sans .release/manifest.json sur la branche : rien n'est fait. Le git et l'API passent par l'objet Deps, que les tests remplacent.
Le jeton n'est jamais ecrit : tout texte sorti (journal, FAILED.md, CHECK.md) passe par Deps.scrub.
"""
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API_DEFAUT = 'https://api.github.com'
BOT_NOM = 'github-actions[bot]'
BOT_MAIL = '41898283+github-actions[bot]@users.noreply.github.com'
TAILLE_MAX = 2 * 1024 ** 3
RELEASE_DIR = '.release'
CLES_MANIFESTE = ('tag', 'title', 'commit', 'assets')
CLES_ASSET = ('name', 'size', 'sha256')
RE_TAG = re.compile(r'BM-V\d+\.\d+\.\d+')
RE_BRANCHE_PUB = re.compile(r'release/(BM-V\d+\.\d+\.\d+)')
RE_BRANCHE_ESSAI = re.compile(r'release/essai-[A-Za-z0-9._-]+')
RE_COMMIT = re.compile(r'[0-9a-f]{40}')
RE_SHA = re.compile(r'[0-9a-f]{64}')
RE_ASSET = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*')


class Echec(Exception):
    """Un controle ou une etape de la publication a echoue ; `quoi` le nomme."""

    def __init__(self, quoi, message):
        super().__init__(message)
        self.quoi = quoi
        self.message = message


class Git:
    def __init__(self, cwd):
        self.cwd = str(cwd)

    def run(self, *args, check=True):
        p = subprocess.run(['git', *args], cwd=self.cwd, capture_output=True, text=True, encoding='utf-8', errors='replace')
        if check and p.returncode != 0:
            raise RuntimeError(f'git {args[0]} failed: {(p.stderr or p.stdout).strip()}')
        return p


class Api:
    """API REST de GitHub. request(methode, chemin_ou_url, corps_json=None, fichier=None) -> (statut, objet JSON ou None)."""

    def __init__(self, token, base=API_DEFAUT):
        self.token = token
        self.base = base

    def request(self, methode, chemin, corps=None, fichier=None):
        url = chemin if chemin.startswith('https://') else self.base + chemin
        h = {'Authorization': f'Bearer {self.token}', 'Accept': 'application/vnd.github+json',
             'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'basemaps-release'}
        data = None
        if corps is not None:
            data = json.dumps(corps).encode('utf-8')
            h['Content-Type'] = 'application/json'
        elif fichier is not None:
            data = open(fichier, 'rb')
            h['Content-Type'] = 'application/octet-stream'
            h['Content-Length'] = str(os.path.getsize(fichier))
        try:
            req = urllib.request.Request(url, data=data, headers=h, method=methode)
            try:
                with urllib.request.urlopen(req, timeout=600) as r:
                    statut, brut = r.status, r.read()
            except urllib.error.HTTPError as e:
                statut, brut = e.code, e.read()
        finally:
            if hasattr(data, 'close'):
                data.close()
        try:
            return statut, (json.loads(brut.decode('utf-8')) if brut else None)
        except ValueError:
            return statut, None


class Deps:
    """Tout ce que les tests remplacent."""

    def __init__(self, git, api, repo, token='', now=time.time):
        self.git = git
        self.api = api
        self.repo = repo
        self.token = token
        self.now = now
        self.root = Path(git.cwd)
        self.joints = {}        # nom d'asset -> fichier joint (temporaire, hors du depot)
        self.tmp = None

    def dossier_temp(self):
        if self.tmp is None:
            self.tmp = Path(tempfile.mkdtemp(prefix='release-', dir=os.environ.get('RUNNER_TEMP') or None))
        return self.tmp

    def nettoyer(self):
        """Supprime les fichiers joints (reussite ou echec)."""
        if self.tmp is not None:
            shutil.rmtree(self.tmp, ignore_errors=True)
        self.tmp = None
        self.joints = {}

    def scrub(self, texte):
        texte = str(texte)
        if self.token:
            for secret in (self.token, base64.b64encode(f'x-access-token:{self.token}'.encode()).decode()):
                texte = texte.replace(secret, '***')
        return texte

    def log(self, texte):
        print(self.scrub(texte), flush=True)

    def utc(self):
        return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(self.now()))


def _msg_api(statut, objet):
    m = objet.get('message') if isinstance(objet, dict) else None
    return f'HTTP {statut}' + (f': {m}' if m else '')


def _appel(deps, quoi, methode, chemin, corps=None, fichier=None, ok=(200, 201, 204)):
    """Appel d'API ; un statut hors `ok` ou une exception reseau est un Echec(quoi)."""
    try:
        statut, objet = deps.api.request(methode, chemin, corps=corps, fichier=fichier)
    except Exception as e:
        raise Echec(quoi, f'{methode} request failed: {deps.scrub(e)}')
    if statut not in ok:
        raise Echec(quoi, f'{methode} request failed ({_msg_api(statut, objet)})')
    return statut, objet


# ------------------------------------------------------------------ les controles

def _manifeste(brut, branche):
    """Controle 1 : (manifeste, essai)."""
    if RE_BRANCHE_PUB.fullmatch(branche):
        essai = False
    elif RE_BRANCHE_ESSAI.fullmatch(branche):
        essai = True
    else:
        raise Echec('1', f'branch name "{branche}" is neither release/BM-V<x.y.z> nor release/essai-<name>')
    try:
        m = json.loads(brut)
    except ValueError as e:
        raise Echec('1', f'manifest.json is not valid JSON ({e})')
    if not isinstance(m, dict) or set(m) != set(CLES_MANIFESTE):
        raise Echec('1', f'manifest.json keys must be exactly {", ".join(CLES_MANIFESTE)}')
    if not isinstance(m['tag'], str) or not RE_TAG.fullmatch(m['tag']):
        raise Echec('1', 'manifest tag must look like BM-V<x.y.z>')
    if not isinstance(m['title'], str) or not m['title'].strip():
        raise Echec('1', 'manifest title must be a non-empty string')
    if not isinstance(m['commit'], str) or not RE_COMMIT.fullmatch(m['commit']):
        raise Echec('1', 'manifest commit must be 40 lowercase hex characters')
    if not isinstance(m['assets'], list):
        raise Echec('1', 'manifest assets must be a list')
    for a in m['assets']:
        if (not isinstance(a, dict) or set(a) != set(CLES_ASSET) or not isinstance(a['name'], str)
                or isinstance(a['size'], bool) or not isinstance(a['size'], int) or not isinstance(a['sha256'], str)
                or not RE_SHA.fullmatch(a['sha256'])):
            raise Echec('1', 'each manifest asset must be {name, size (integer), sha256 (64 hex)} and nothing else')
    if not essai and RE_BRANCHE_PUB.fullmatch(branche).group(1) != m['tag']:
        raise Echec('1', f'branch {branche} does not match manifest tag {m["tag"]}')
    return m, essai


def _controle_commit(deps, m, essai):
    """Controle 2 : (message, C est la tete de main)."""
    git, c = deps.git, m['commit']
    if git.run('cat-file', '-e', f'{c}^{{commit}}', check=False).returncode != 0:
        raise Echec('2', f'commit {c} is not in the repository')
    if git.run('merge-base', '--is-ancestor', c, 'HEAD', check=False).returncode != 0:
        raise Echec('2', f'commit {c} is not an ancestor of the branch head')
    noms = [n for n in git.run('diff', '--name-only', '--no-renames', '-z', c, 'HEAD').stdout.split('\0') if n]
    hors = [n for n in noms if not n.startswith(RELEASE_DIR + '/')]
    if hors:
        raise Echec('2', f'the branch changes files outside {RELEASE_DIR}/: {", ".join(hors[:5])}')
    p = git.run('fetch', '--quiet', 'origin', '+refs/heads/main:refs/remotes/origin/main', check=False)
    tete = git.run('rev-parse', 'refs/remotes/origin/main', check=False).stdout.strip() if p.returncode == 0 else ''
    est_tete = tete == c
    if not est_tete and not essai:
        raise Echec('2', f'commit {c} is not the head of main ({tete or "main not readable"})')
    return f'commit is an ancestor of the branch head, only {RELEASE_DIR}/ changed; commit is the head of main: ' + \
        ('yes' if est_tete else 'no'), est_tete


def _controle_changelog(deps, m):
    p = deps.git.run('show', f'{m["commit"]}:CHANGELOG.md', check=False)
    if p.returncode != 0:
        raise Echec('3', 'CHANGELOG.md not found at the commit')
    prefixe = f'## {m["tag"]} \u2014 '
    for l in p.stdout.splitlines():
        if l.startswith(prefixe) and re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', l[len(prefixe):]):
            try:
                time.strptime(l[len(prefixe):], '%Y-%m-%d')
            except ValueError:
                continue
            return f'CHANGELOG line {l} found'
    raise Echec('3', f'CHANGELOG.md at the commit has no line "## {m["tag"]} \u2014 YYYY-MM-DD"')


def _controle_tag(deps, m):
    tag, repo = m['tag'], deps.repo
    try:
        statut, objet = deps.api.request('GET', f'/repos/{repo}/git/ref/tags/{tag}')
    except Exception as e:
        raise Echec('4', f'GET request failed: {deps.scrub(e)}')
    if statut == 200:
        raise Echec('4', f'tag {tag} already exists')
    if statut != 404:
        raise Echec('4', f'cannot read tag {tag} ({_msg_api(statut, objet)})')
    page = 1
    while True:
        _, lst = _appel(deps, '4', 'GET', f'/repos/{repo}/releases?per_page=100&page={page}', ok=(200,))
        if not isinstance(lst, list):
            raise Echec('4', 'unexpected answer when listing releases')
        if any(r.get('tag_name') == tag for r in lst):
            raise Echec('4', f'a release (maybe a draft) already carries the tag {tag}')
        if len(lst) < 100:
            break
        page += 1
    return f'tag {tag} is free'


def _sha256(chemin):
    h = hashlib.sha256()
    with open(chemin, 'rb') as f:
        for bloc in iter(lambda: f.read(1 << 20), b''):
            h.update(bloc)
    return h.hexdigest()


def _morceaux(dossier, nom):
    """Les morceaux `<nom>.partNNN` presents : liste triee de (numero, chemin)."""
    re_p = re.compile(re.escape(nom) + r'\.part(\d{3})')
    trouves = []
    if dossier.is_dir():
        for p in dossier.iterdir():
            mo = re_p.fullmatch(p.name)
            if mo and p.is_file():
                trouves.append((int(mo.group(1)), p))
    return sorted(trouves)


def _joindre(deps, nom, morceaux):
    cible = deps.dossier_temp() / nom
    with open(cible, 'wb') as out:
        for _, p in morceaux:
            with open(p, 'rb') as f:
                shutil.copyfileobj(f, out, 1 << 20)
    return cible


def _controle_assets(deps, m):
    dossier = deps.root / RELEASE_DIR / 'assets'
    assets = m['assets']
    noms = [a['name'] for a in assets]
    if len(set(noms)) != len(noms):
        raise Echec('5', 'an asset is listed twice')
    attendus = set(noms)
    en_parts = 0
    for a in assets:
        n = a['name']
        if not RE_ASSET.fullmatch(n):
            raise Echec('5', f'asset name "{n}" does not match [A-Za-z0-9][A-Za-z0-9._-]*')
        f = dossier / n
        morceaux = _morceaux(dossier, n)
        if f.is_file() and morceaux:
            raise Echec('5', f'asset {n} is present both whole and in parts ({morceaux[0][1].name}...)')
        if morceaux:
            for i, (num, p) in enumerate(morceaux, 1):
                if num != i:
                    raise Echec('5', f'asset {n}: the parts are not a continuous run from part001 (expected {n}.part{i:03d}, found {p.name})')
                attendus.add(p.name)
            f = _joindre(deps, n, morceaux)
            deps.joints[n] = f
            en_parts += 1
        elif not f.is_file():
            raise Echec('5', f'asset {n} is not in {RELEASE_DIR}/assets/')
        else:
            attendus.add(n)
        taille = f.stat().st_size
        if taille != a['size']:
            raise Echec('5', f'asset {n}: size {taille} differs from the manifest ({a["size"]})')
        if taille >= TAILLE_MAX:
            raise Echec('5', f'asset {n} is 2 GiB or more')
        if _sha256(f) != a['sha256']:
            raise Echec('5', f'asset {n}: SHA-256 differs from the manifest')
    en_trop = sorted(p.name for p in dossier.iterdir() if p.name not in attendus) if dossier.is_dir() else []
    if en_trop:
        raise Echec('5', f'files in {RELEASE_DIR}/assets/ not listed in the manifest: {", ".join(en_trop[:5])}')
    if not assets:
        return 'no asset'
    return f'{len(assets)} asset(s) match the manifest' + (f' ({en_parts} joined from parts)' if en_parts else '')


def _controle_notes(deps):
    f = deps.root / RELEASE_DIR / 'notes.md'
    if not f.is_file():
        raise Echec('6', f'{RELEASE_DIR}/notes.md is missing')
    try:
        texte = f.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        raise Echec('6', 'notes.md is not valid UTF-8')
    if not texte.strip():
        raise Echec('6', 'notes.md is empty')
    return 'notes.md is not empty'


def _paragraphe_changelog(deps, m):
    """Le bloc qui suit la ligne `### Version numbers` du CHANGELOG au commit, jusqu'au titre `## ` suivant (`## Unreleased`, ou, une fois la
    release nommee, `## BM-V<x.y.z> - AAAA-MM-JJ`), exclu, strip()."""
    p = deps.git.run('show', f'{m["commit"]}:CHANGELOG.md', check=False)
    manque = Echec('7', 'CHANGELOG.md at the commit has no "### Version numbers" block before "## Unreleased"')
    if p.returncode != 0:
        raise manque
    bloc, dedans = [], False
    for l in p.stdout.split('\n'):
        if not dedans:
            if l.rstrip() == '### Version numbers':
                dedans = True
            elif l.startswith('## '):
                break
        elif l.startswith('## '):
            break
        else:
            bloc.append(l)
    texte = '\n'.join(bloc).strip() if dedans else ''
    if not texte:
        raise manque
    return texte


def _controle_numerotation(deps, m):
    """Controle 7 : les notes finissent par un paragraphe `## Version numbers` egal, mot pour mot, a l'en-tete du CHANGELOG."""
    source = _paragraphe_changelog(deps, m)
    lignes = (deps.root / RELEASE_DIR / 'notes.md').read_text(encoding='utf-8').split('\n')
    k = max((i for i, l in enumerate(lignes) if l.rstrip() == '## Version numbers'), default=-1)
    if k < 0:
        raise Echec('7', 'notes.md has no "## Version numbers" paragraph')
    if '\n'.join(lignes[k + 1:]).strip() != source:
        raise Echec('7', 'the "Version numbers" paragraph of notes.md differs from the CHANGELOG header')
    return '"Version numbers" paragraph matches the CHANGELOG header'


def controles(deps, branche, brut, lignes):
    """Les sept controles dans l'ordre ; ajoute une ligne par controle execute a `lignes` ; leve Echec au premier defaut.
    Renvoie (manifeste, essai)."""
    def ligne(n, etat, msg):
        lignes.append(f'{n}. {etat} - {msg}')

    try:
        m, essai = _manifeste(brut, branche)
        ligne(1, 'OK', f'branch {branche}, tag {m["tag"]}')
        msg, _ = _controle_commit(deps, m, essai)
        ligne(2, 'OK', msg)
        ligne(3, 'OK', _controle_changelog(deps, m))
        ligne(4, 'OK', _controle_tag(deps, m))
        ligne(5, 'OK', _controle_assets(deps, m))
        ligne(6, 'OK', _controle_notes(deps))
        ligne(7, 'OK', _controle_numerotation(deps, m))
    except Echec as e:
        ligne(e.quoi, 'FAILED', deps.scrub(e.message))
        raise
    return m, essai


# ------------------------------------------------------------------ la publication

def _tag_sur(deps, tag):
    """Sha du commit vise par le tag (suit un tag annote)."""
    repo = deps.repo
    _, ref = _appel(deps, 'read-back', 'GET', f'/repos/{repo}/git/ref/tags/{tag}', ok=(200,))
    obj = (ref or {}).get('object') or {}
    if obj.get('type') == 'tag':
        _, t = _appel(deps, 'read-back', 'GET', f'/repos/{repo}/git/tags/{obj.get("sha")}', ok=(200,))
        obj = (t or {}).get('object') or {}
    return obj.get('sha')


def publier(deps, branche, m, etat):
    """Les cinq etapes. `etat` : {'brouillon': id ou None} pour que l'appelant sache quoi nettoyer."""
    repo, tag, c = deps.repo, m['tag'], m['commit']
    notes = (deps.root / RELEASE_DIR / 'notes.md').read_text(encoding='utf-8')
    _, rel = _appel(deps, 'step 1 (create the draft)', 'POST', f'/repos/{repo}/releases',
                    corps={'tag_name': tag, 'target_commitish': c, 'name': m['title'], 'body': notes,
                           'draft': True, 'prerelease': False})
    rid = (rel or {}).get('id')
    if not rid:
        raise Echec('step 1 (create the draft)', 'the answer has no release id')
    etat['brouillon'] = rid
    haut = (rel.get('upload_url') or '').split('{')[0] or f'https://uploads.github.com/repos/{repo}/releases/{rid}/assets'
    for a in m['assets']:
        _appel(deps, f'step 2 (upload {a["name"]})', 'POST', f'{haut}?name={urllib.parse.quote(a["name"])}',
               fichier=deps.joints.get(a['name']) or deps.root / RELEASE_DIR / 'assets' / a['name'])
    _, lst = _appel(deps, 'step 2 (read back the files)', 'GET', f'/repos/{repo}/releases/{rid}/assets?per_page=100', ok=(200,))
    vus = sorted((x.get('name'), x.get('size')) for x in (lst or []))
    voulus = sorted((a['name'], a['size']) for a in m['assets'])
    if vus != voulus:
        raise Echec('step 2 (read back the files)', f'files on the draft {vus} differ from the manifest {voulus}')
    _appel(deps, 'step 3 (publish)', 'PATCH', f'/repos/{repo}/releases/{rid}', corps={'draft': False, 'make_latest': 'true'})
    etat['publiee'] = True
    _, rel = _appel(deps, 'step 4 (read back the release)', 'GET', f'/repos/{repo}/releases/tags/{tag}', ok=(200,))
    if not isinstance(rel, dict) or rel.get('draft') or rel.get('tag_name') != tag:
        raise Echec('step 4 (read back the release)', 'the release is not published under the tag')
    sha = _tag_sur(deps, tag)
    if sha != c:
        raise Echec('step 4 (read back the release)', f'tag {tag} points to {sha}, not to {c}')
    p = deps.git.run('push', 'origin', '--delete', branche, check=False)
    if p.returncode != 0:
        raise Echec('step 5 (delete the branch)', f'git push --delete failed: {deps.scrub((p.stderr or p.stdout).strip())}')


def _pousser_fichier(deps, branche, nom, texte, message):
    """Committe .release/<nom> (auteur : le robot) et le pousse sur la branche, en avance rapide."""
    dossier = deps.root / RELEASE_DIR
    dossier.mkdir(exist_ok=True)
    (dossier / nom).write_text(deps.scrub(texte), encoding='utf-8', newline='\n')
    g = deps.git
    g.run('add', '-f', '--', f'{RELEASE_DIR}/{nom}')
    g.run('-c', f'user.name={BOT_NOM}', '-c', f'user.email={BOT_MAIL}', 'commit', '-q', '-m', message)
    g.run('push', 'origin', f'HEAD:refs/heads/{branche}')


def _tenter_pousser(deps, branche, nom, texte, message):
    try:
        _pousser_fichier(deps, branche, nom, texte, message)
    except Exception as e:
        deps.log(f'could not push {nom} to {branche}: {e}')


def run(deps, branche):
    """Code de sortie : 0 ou 1. Les fichiers joints sont supprimes a la fin, reussite ou echec."""
    try:
        return _run(deps, branche)
    finally:
        deps.nettoyer()


def _run(deps, branche):
    manifeste = deps.root / RELEASE_DIR / 'manifest.json'
    if not manifeste.is_file():
        deps.log('Waiting for the manifest (.release/manifest.json): nothing to do.')
        return 0
    lignes, etat = [], {}
    essai = branche.startswith('release/essai-')
    try:
        try:
            brut = manifeste.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            brut = ''
        m, essai = controles(deps, branche, brut, lignes)
        for l in lignes:
            deps.log(l)
        if essai:
            _tenter_pousser(deps, branche, 'CHECK.md', '\n'.join(lignes) + '\n', 'Release check: all controls passed')
            return 0
        publier(deps, branche, m, etat)
        deps.log(f'Release {m["tag"]} published; branch {branche} deleted.')
        return 0
    except Echec as e:
        message = deps.scrub(e.message)
        for l in lignes:
            deps.log(l)
        deps.log(f'FAILED at {e.quoi}: {message}')
        if essai:
            _tenter_pousser(deps, branche, 'CHECK.md', '\n'.join(lignes) + '\n', 'Release check: a control failed')
            return 1
        suite = ''
        if etat.get('brouillon') and not etat.get('publiee'):
            try:
                _appel(deps, 'delete the draft', 'DELETE', f'/repos/{deps.repo}/releases/{etat["brouillon"]}')
                suite = 'The draft release was deleted.'
            except Echec as e2:
                suite = f'The draft release could not be deleted: {deps.scrub(e2.message)}'
        elif etat.get('publiee'):
            suite = 'The release was already published and is left as it is.'
        texte = (f'# Release failed\n\n- Control or step: {e.quoi}\n- Message: {message}\n'
                 + (f'- {suite}\n' if suite else '') + f'- Time (UTC): {deps.utc()}\n')
        _tenter_pousser(deps, branche, 'FAILED.md', texte, 'Release failed: see .release/FAILED.md')
        return 1


def main():
    token = os.environ.get('GITHUB_TOKEN', '')
    repo = os.environ['GITHUB_REPOSITORY']
    branche = os.environ['GITHUB_REF_NAME']
    deps = Deps(Git(os.getcwd()), Api(token, os.environ.get('GITHUB_API_URL', API_DEFAUT)), repo, token)
    return run(deps, branche)


if __name__ == '__main__':
    sys.exit(main())

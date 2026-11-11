import json
import os
import shutil
import subprocess

import pytest

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.mark.skipif(shutil.which("node") is None, reason="Node absent")
def test_charger_style_resout_les_chemins_contre_l_url_du_style():
    script = """
    const { chargerStyle } = require(%r);
    globalThis.fetch = async (u) => ({ ok: true, json: async () => ({
      sources: { fond: { url: 'pmtiles://./fond.pmtiles' }, relief: { url: 'pmtiles://./relief.pmtiles' } },
      sprite: './sprite/papier', glyphs: './glyphs/{fontstack}/{range}.pbf' }) });
    chargerStyle('https://exemple.org/fonds/syria/style-clair.json').then((s) => console.log(JSON.stringify(s)));
    """ % os.path.join(RACINE, "web", "charger-style.js")
    sortie = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout
    s = json.loads(sortie)
    assert s["sources"]["fond"]["url"] == "pmtiles://https://exemple.org/fonds/syria/fond.pmtiles"
    assert s["sources"]["relief"]["url"] == "pmtiles://https://exemple.org/fonds/syria/relief.pmtiles"
    assert s["sprite"] == "https://exemple.org/fonds/syria/sprite/papier"
    assert s["glyphs"] == "https://exemple.org/fonds/syria/glyphs/{fontstack}/{range}.pbf"


def test_charger_style_est_court_et_sans_dependance():
    src = open(os.path.join(RACINE, "web", "charger-style.js"), encoding="utf-8").read()
    assert "import " not in src and "require(" not in src
    assert len([l for l in src.splitlines() if l.strip() and not l.strip().startswith("//")]) <= 30

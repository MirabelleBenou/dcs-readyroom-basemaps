// Charge un style par son URL et résout ses chemins relatifs contre elle (sources, sprite, glyphes).
// Sans dépendance. Usage : const style = await chargerStyle('/fonds/syria/style-clair.json'); new maplibregl.Map({ style, ... })
(function (racine) {
  async function chargerStyle(url) {
    const base = new URL(url, racine.location ? racine.location.href : undefined).href;
    const rep = await fetch(base);
    if (!rep.ok) throw new Error('style introuvable : ' + base + ' (' + rep.status + ')');
    const style = await rep.json();
    const abs = (u) => new URL(u, base).href;
    // un gabarit comme ./glyphs/{fontstack}/{range}.pbf : on résout la partie fixe, jamais les accolades
    const gabarit = (u) => { const i = u.indexOf('{'); return i < 0 ? abs(u) : abs(u.slice(0, i)) + u.slice(i); };
    for (const s of Object.values(style.sources || {})) {
      if (typeof s.url !== 'string') continue;
      s.url = s.url.startsWith('pmtiles://') ? 'pmtiles://' + abs(s.url.slice(10)) : abs(s.url);
    }
    if (style.sprite) style.sprite = abs(style.sprite);
    if (style.glyphs) style.glyphs = gabarit(style.glyphs);
    return style;
  }
  racine.chargerStyle = chargerStyle;
  if (typeof module !== 'undefined' && module.exports) module.exports = { chargerStyle };
})(typeof globalThis !== 'undefined' ? globalThis : window);

"""Campaign visualizer. Thin wrapper over the parent OrbitWars visualize.py
that injects campaign-specific HTML/CSS/JS overlays:

 - Lore header above the side stats
 - Strategic territory-graph map in the side panel (click to jump)
 - Cinema title card that fades in at the start of each match
 - Auto-chain between matches in cinema mode

The parent ../visualize.py is the source of truth for everything else (cinema
shots, event detection, board rendering, ultra mode, etc.). This file only
adds the campaign overlay on top of it. When the parent updates, this file
should pick up the changes automatically -- no merging needed.
"""

import html as _html
import importlib.util
import json
import os

# Locate the parent OrbitWars visualize.py. Assume this repo lives as a
# subfolder of the OrbitWars working tree (the layout used today). Load it
# via importlib under a distinct module name so the import doesn't recurse
# into *this* file (which is also called visualize.py).
_PARENT_PATH = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "visualize.py")
)
if not os.path.exists(_PARENT_PATH):
    raise RuntimeError(
        f"Parent OrbitWars visualize.py not found at {_PARENT_PATH}. "
        "The campaign repo expects to live as a subfolder of OrbitWars."
    )
_spec = importlib.util.spec_from_file_location("_orbitwars_parent_visualize", _PARENT_PATH)
_parent_viz = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_parent_viz)

# Re-export the parent's helpers so campaign.py imports keep working.
_build_match = _parent_viz._build_match
write_html = _parent_viz.write_html
write_tournament_html = _parent_viz.write_tournament_html


# ---------------------------------------------------------------------------
# CSS overlay (appended before </style> in the parent template)
# ---------------------------------------------------------------------------
_CAMPAIGN_CSS = r"""
  /* --- campaign overlays --- */
  #loreHeader { display: none; background: #1a223e; padding: 8px 10px;
    border-radius: 6px; border-left: 3px solid #ffd966; margin-bottom: 4px; }
  #loreHeader .terr-title { font-size: 14px; font-weight: 700; }
  #loreHeader .terr-meta { font-size: 11px; color: #8a96b8; margin-top: 2px; }
  #loreHeader .terr-status { display: inline-block; padding: 1px 6px;
    border-radius: 8px; font-size: 10px; font-weight: 700; margin-left: 6px;
    vertical-align: middle; }
  .status-won { background: #3ad17a; color: #000; }
  .status-lost { background: #ff5a5a; color: #fff; }
  .status-current { background: #ffd966; color: #000; }
  .status-upcoming { background: #444; color: #ccc; }
  #campaignMapWrap { display: none; background: #0a0f22; border-radius: 6px;
    padding: 8px; }
  #campaignMapWrap .title { color: #8a96b8; font-size: 10px;
    text-transform: uppercase; letter-spacing: 1px; margin-bottom: 4px; }
  #campaignMap { width: 100%; height: 200px; display: block; cursor: pointer; }
  #cinemaTitleCard { display: none; position: fixed; top: 12vh; left: 0;
    right: 0; text-align: center; pointer-events: none; z-index: 30;
    opacity: 0; transition: opacity 0.6s ease; }
  #cinemaTitleCard.show { opacity: 1; }
  #cinemaTitleCard .ep { font-size: 13px; color: #8a96b8; letter-spacing: 6px;
    text-transform: uppercase; font-weight: 600; }
  #cinemaTitleCard .name { font-size: 64px; font-weight: 800; color: #fff;
    text-shadow: 0 4px 32px rgba(0,0,0,0.9), 0 0 8px rgba(255,217,102,0.3);
    letter-spacing: 4px; margin: 8px 0; }
  #cinemaTitleCard .lore { font-size: 18px; color: #c8d0e8; font-style: italic;
    max-width: 800px; margin: 12px auto 0; text-shadow: 0 2px 8px rgba(0,0,0,0.8); }
  #cinemaTitleCard .npc { font-size: 15px; color: #ffd966; margin-top: 16px;
    letter-spacing: 2px; text-shadow: 0 2px 8px rgba(0,0,0,0.8); }
  #cinemaTitleCard .boss-tag { color: #ff5a5a; font-weight: 700; }
"""


# ---------------------------------------------------------------------------
# HTML overlay snippets
# ---------------------------------------------------------------------------
_CAMPAIGN_SIDE_HTML = """    <div id="loreHeader"></div>
    <div id="campaignMapWrap"><div class="title">Campaign Map</div>
      <canvas id="campaignMap"></canvas></div>
"""

_CAMPAIGN_TOP_HTML = """  <div id="cinemaTitleCard"></div>
"""


# ---------------------------------------------------------------------------
# JS overlay (appended before </script> in the parent's script block).
# Wraps parent functions instead of editing them in place so the parent can
# evolve without forcing us to re-merge.
# ---------------------------------------------------------------------------
_CAMPAIGN_JS = r"""
// =====================================================================
// CAMPAIGN OVERLAY (injected by orbit-wars-campaign/visualize.py)
// =====================================================================
const CAMPAIGN = (typeof DATA !== 'undefined' && DATA.campaign) ? DATA.campaign : null;
const loreHeaderEl    = document.getElementById('loreHeader');
const campaignMapWrap = document.getElementById('campaignMapWrap');
const campaignMapEl   = document.getElementById('campaignMap');
const cinemaTitleCardEl = document.getElementById('cinemaTitleCard');

if (CAMPAIGN) {
  if (loreHeaderEl)    loreHeaderEl.style.display = '';
  if (campaignMapWrap) campaignMapWrap.style.display = '';
}

function _matchTerritoryId(matchIdx) {
  const m = MATCHES[matchIdx];
  return m && m.territory_id ? m.territory_id : null;
}

function _territoryStatus(tid) {
  for (let i = 0; i < MATCHES.length; i++) {
    if (MATCHES[i].territory_id === tid) {
      if (i === currentMatchIdx) return 'current';
      return MATCHES[i].won ? 'won' : 'lost';
    }
  }
  return 'upcoming';
}

function _territoryPos(tid, W, H) {
  const terr = CAMPAIGN.territories[tid];
  if (terr && terr.pos) {
    return [terr.pos[0] * (W - 40) + 20, terr.pos[1] * (H - 40) + 20];
  }
  const ids = Object.keys(CAMPAIGN.territories);
  const i = ids.indexOf(tid);
  const a = (i / Math.max(1, ids.length)) * Math.PI * 2 - Math.PI / 2;
  return [W / 2 + Math.cos(a) * (W / 2 - 30), H / 2 + Math.sin(a) * (H / 2 - 30)];
}

function renderLoreHeader() {
  if (!CAMPAIGN || !loreHeaderEl) return;
  const tid = _matchTerritoryId(currentMatchIdx);
  if (!tid) { loreHeaderEl.style.display = 'none'; return; }
  const terr = CAMPAIGN.territories[tid];
  if (!terr) { loreHeaderEl.style.display = 'none'; return; }
  const m = MATCHES[currentMatchIdx];
  const stat = _territoryStatus(tid);
  const ep = m.episode ? `Ep ${m.episode}` : '';
  const bossTag = terr.boss ? ' <span style="color:#ff5a5a">[BOSS]</span>' : '';
  const niceName = tid.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
  loreHeaderEl.style.display = '';
  loreHeaderEl.innerHTML = `
    <div class="terr-title">${ep ? ep + ' &middot; ' : ''}${niceName}${bossTag}
      <span class="terr-status status-${stat}">${stat.toUpperCase()}</span></div>
    <div class="terr-meta">vs ${terr.npc || '?'} &middot; seed ${m.seed ?? '?'}</div>
  `;
}

function drawCampaignMap() {
  if (!CAMPAIGN || !campaignMapEl) return;
  const canvas = campaignMapEl;
  const ctx = canvas.getContext('2d');
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.max(1, Math.floor(rect.width));
  canvas.height = Math.max(1, Math.floor(rect.height));
  const W = canvas.width, H = canvas.height;
  ctx.fillStyle = '#0a0f22';
  ctx.fillRect(0, 0, W, H);
  const ids = Object.keys(CAMPAIGN.territories);

  ctx.strokeStyle = '#2a3560';
  ctx.lineWidth = 1.5;
  const drawn = new Set();
  for (const tid of ids) {
    for (const n of (CAMPAIGN.territories[tid].neighbors || [])) {
      if (!CAMPAIGN.territories[n]) continue;
      const key = [tid, n].sort().join('|');
      if (drawn.has(key)) continue;
      drawn.add(key);
      const [x1, y1] = _territoryPos(tid, W, H);
      const [x2, y2] = _territoryPos(n, W, H);
      ctx.beginPath();
      ctx.moveTo(x1, y1); ctx.lineTo(x2, y2);
      ctx.stroke();
    }
  }

  for (const tid of ids) {
    const [x, y] = _territoryPos(tid, W, H);
    const terr = CAMPAIGN.territories[tid];
    const stat = _territoryStatus(tid);
    const r = terr.boss ? 14 : 10;
    let fill, stroke;
    if (stat === 'won')      { fill = '#3ad17a'; stroke = '#1a8050'; }
    else if (stat === 'lost')    { fill = '#ff5a5a'; stroke = '#a02020'; }
    else if (stat === 'current') { fill = '#ffd966'; stroke = '#fff'; }
    else                         { fill = '#2a3560'; stroke = '#444a6a'; }
    ctx.fillStyle = fill;
    ctx.strokeStyle = stroke;
    ctx.lineWidth = stat === 'current' ? 2.5 : 1.2;
    ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    if (terr.boss) {
      ctx.fillStyle = '#ffd966';
      ctx.beginPath();
      ctx.moveTo(x, y - r - 9);
      ctx.lineTo(x - 5, y - r - 1);
      ctx.lineTo(x - 2, y - r - 4);
      ctx.lineTo(x, y - r - 1);
      ctx.lineTo(x + 2, y - r - 4);
      ctx.lineTo(x + 5, y - r - 1);
      ctx.closePath(); ctx.fill();
    }
    ctx.fillStyle = stat === 'upcoming' ? '#7a86a8' : '#e8edf7';
    ctx.font = stat === 'current' ? 'bold 10px sans-serif' : '10px sans-serif';
    ctx.textAlign = 'center'; ctx.textBaseline = 'top';
    ctx.fillText(tid.replace(/_/g, ' '), x, y + r + 3);
  }
}

function campaignMapClick(e) {
  if (!CAMPAIGN || !campaignMapEl) return;
  const rect = campaignMapEl.getBoundingClientRect();
  const mx = (e.clientX - rect.left) * (campaignMapEl.width / rect.width);
  const my = (e.clientY - rect.top) * (campaignMapEl.height / rect.height);
  const W = campaignMapEl.width, H = campaignMapEl.height;
  for (const tid of Object.keys(CAMPAIGN.territories)) {
    const [x, y] = _territoryPos(tid, W, H);
    const terr = CAMPAIGN.territories[tid];
    const r = terr.boss ? 14 : 10;
    if (Math.hypot(mx - x, my - y) <= r + 3) {
      for (let i = 0; i < MATCHES.length; i++) {
        if (MATCHES[i].territory_id === tid) { loadMatch(i); return; }
      }
      return;
    }
  }
}
if (campaignMapEl) campaignMapEl.onclick = campaignMapClick;

let _titleCardTimers = [];
function showCinemaTitleCard() {
  if (!CAMPAIGN || !cinemaTitleCardEl) return;
  const tid = _matchTerritoryId(currentMatchIdx);
  if (!tid) return;
  const terr = CAMPAIGN.territories[tid];
  if (!terr) return;
  const m = MATCHES[currentMatchIdx];
  const niceName = tid.replace(/_/g, ' ').toUpperCase();
  const epLabel = terr.boss ? '<span class="boss-tag">FINAL BATTLE</span>'
    : `EPISODE ${m.episode ?? (currentMatchIdx + 1)}`;
  cinemaTitleCardEl.innerHTML = `
    <div class="ep">${epLabel}</div>
    <div class="name">${niceName}</div>
    ${terr.lore ? `<div class="lore">"${terr.lore}"</div>` : ''}
    <div class="npc">vs ${(terr.npc || '?').toUpperCase()}</div>
  `;
  for (const t of _titleCardTimers) clearTimeout(t);
  _titleCardTimers = [];
  cinemaTitleCardEl.style.display = 'block';
  void cinemaTitleCardEl.offsetWidth;
  cinemaTitleCardEl.classList.add('show');
  _titleCardTimers.push(setTimeout(() => {
    cinemaTitleCardEl.classList.remove('show');
    _titleCardTimers.push(setTimeout(() => {
      cinemaTitleCardEl.style.display = 'none';
    }, 700));
  }, 3200));
}

// --- Wrap parent functions so the parent stays editable in place ---

if (typeof loadMatch === 'function') {
  const _origLoadMatch = loadMatch;
  loadMatch = function(idx) {
    _origLoadMatch(idx);
    if (CAMPAIGN) { renderLoreHeader(); drawCampaignMap(); }
  };
}

if (typeof startCinema === 'function') {
  const _origStartCinema = startCinema;
  startCinema = function() {
    _origStartCinema();
    showCinemaTitleCard();
  };
}

if (typeof stopCinema === 'function') {
  const _origStopCinema = stopCinema;
  stopCinema = function() {
    for (const t of _titleCardTimers) clearTimeout(t);
    _titleCardTimers = [];
    if (cinemaTitleCardEl) {
      cinemaTitleCardEl.classList.remove('show');
      cinemaTitleCardEl.style.display = 'none';
    }
    _origStopCinema();
  };
}

if (typeof advanceShot === 'function') {
  const _origAdvanceShot = advanceShot;
  advanceShot = function() {
    // Detect "this call would have run off the end of the shot list" --
    // peek before delegating. If we still have a campaign match to go to,
    // load it and rebuild cinema state in place.
    if (cinemaState && cinemaState.shotIdx + 1 >= cinemaState.shots.length &&
        CAMPAIGN && currentMatchIdx < MATCHES.length - 1) {
      cinemaState.shotIdx++;
      cinemaState.shotFrame = 0;
      loadMatch(currentMatchIdx + 1);
      cinemaState = buildCinema();
      const s0 = cinemaState.shots[0];
      if (s0 && s0.target) {
        cinemaState.cam.cx = cinemaState.cam.tcx = s0.target.cx;
        cinemaState.cam.cy = cinemaState.cam.tcy = s0.target.cy;
        cinemaState.cam.zoom = cinemaState.cam.tzoom = s0.target.zoom;
      }
      showCinemaTitleCard();
      return true;
    }
    return _origAdvanceShot();
  };
}

if (typeof resizeCanvases === 'function') {
  const _origResize = resizeCanvases;
  resizeCanvases = function() {
    _origResize();
    if (CAMPAIGN) drawCampaignMap();
  };
}

// Initial render now that DATA is in scope
if (CAMPAIGN) { renderLoreHeader(); drawCampaignMap(); }
// === END CAMPAIGN OVERLAY ===
"""


# ---------------------------------------------------------------------------
# Template patching
# ---------------------------------------------------------------------------
def _patch_template(template):
    """Inject campaign CSS / HTML / JS into the parent's _TEMPLATE.
    Uses uniquely-identifiable anchor strings; if the parent renames any of
    these, the patch raises so the failure is loud (not silent)."""
    anchors = [
        ("</style>", _CAMPAIGN_CSS + "\n</style>"),
        ('<div id="sideContent"', _CAMPAIGN_SIDE_HTML + '    <div id="sideContent"'),
        ('<button id="cinemaExit"', _CAMPAIGN_TOP_HTML + '  <button id="cinemaExit"'),
        ("</script>", _CAMPAIGN_JS + "\n</script>"),
    ]
    out = template
    for needle, replacement in anchors:
        if needle not in out:
            raise RuntimeError(
                f"Campaign visualizer can't patch parent template: anchor "
                f"{needle!r} not found. Parent ../visualize.py may have changed."
            )
        out = out.replace(needle, replacement, 1)
    return out


# ---------------------------------------------------------------------------
# Public API used by campaign.py
# ---------------------------------------------------------------------------
def _write_multi(out_path, matches, campaign_meta=None, auto_cinema=False):
    """Write a campaign HTML viewer. matches is a list of payloads from
    _build_match (each augmented with territory_id/episode/won by the caller).
    campaign_meta is the campaign-level graph + lore metadata."""
    payload = {"matches": matches, "autoCinema": bool(auto_cinema)}
    if campaign_meta is not None:
        payload["campaign"] = campaign_meta
    patched = _patch_template(_parent_viz._TEMPLATE)
    html_out = patched.replace(
        "__PAYLOAD__", _html.escape(json.dumps(payload), quote=False)
    )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_out)
    return os.path.abspath(out_path)

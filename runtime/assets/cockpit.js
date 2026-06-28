const BASE = '/api';
const nativeFetch = window.fetch.bind(window);
window.fetch = (input, init = {}) => {
  const method = (init.method || 'GET').toUpperCase();
  const url = typeof input === 'string' ? input : input.url;
  if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(method) && new URL(url, location.href).origin === location.origin) {
    const csrf = document.cookie.split('; ').find(item => item.startsWith('spawnwp_csrf='));
    init.headers = new Headers(init.headers || {});
    if (csrf) init.headers.set('X-CSRF-Token', decodeURIComponent(csrf.split('=').slice(1).join('=')));
  }
  return nativeFetch(input, init);
};
const dbCache = {};   // name -> {size_mb, tables}
const projectSignatures = {}; // name -> last structural/status payload
let BLUEPRINTS = [];
let SYS_BUSY = false; // true when a build is running / high load (guardrail)
let DEPLOY_ACTIVE = false;
let PLATFORM = {};

async function loadPlatform() {
  try {
    PLATFORM = await fetch(`${BASE}/platform`, { cache: 'no-store' }).then(response => response.json());
    const domain = document.getElementById('domain-preview');
    if (domain) domain.textContent = PLATFORM.domain || 'domain';
  } catch (error) { /* keep neutral placeholders */ }
}

function blockedIfBusy() {
  if (SYS_BUSY) {
    showToast('System under load: action blocked, try again shortly', true);
    return true;
  }
  return false;
}

const newName = document.getElementById('new-name');
if (newName) newName.addEventListener('input', e => {
  document.getElementById('name-preview').textContent = e.target.value || 'site-name';
});

async function loadBlueprints() {
  const select = document.getElementById('new-blueprint');
  const button = document.getElementById('btn-create');
  const catalog = document.getElementById('blueprint-catalog');
  if (!select || !catalog) return;
  try {
    const response = await fetch(`${BASE}/blueprints`, { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    BLUEPRINTS = payload.blueprints || [];
    if (!BLUEPRINTS.length) throw new Error('No valid blueprints available');
    select.value = BLUEPRINTS.some(item => item.id === 'development') ? 'development' : BLUEPRINTS[0].id;
    catalog.innerHTML = BLUEPRINTS.map(item => `<label class="blueprint-option" data-blueprint="${esc(item.id)}">
      <input type="radio" name="blueprint-choice" value="${esc(item.id)}">
      <div class="blueprint-option-head"><h2>${esc(item.name)}</h2><span class="badge badge-gray">${esc(item.version)}</span></div>
      <p>${esc(item.description)}</p>
      <div class="blueprint-facts"><span>${esc(item.source)}</span><span>${item.debug ? 'debug on' : 'debug off'}</span><span>${esc(item.content_preset)}</span><span>PHP ${item.php.allowed.map(version => version === '7.4' ? '7.4 legacy' : esc(version)).join(', ')}</span></div>
    </label>`).join('');
    catalog.querySelectorAll('.blueprint-option').forEach(option => option.addEventListener('click', () => selectBlueprint(option.dataset.blueprint)));
    selectBlueprint(select.value);
    const errors = payload.errors || [];
    const alert = document.getElementById('blueprint-errors');
    if (errors.length) {
      alert.hidden = false;
      alert.textContent = `${errors.length} invalid custom blueprint${errors.length === 1 ? '' : 's'} ignored.`;
    }
  } catch (error) {
    catalog.innerHTML = '';
    document.getElementById('blueprint-note').textContent = error.message;
    button.disabled = true;
  }
}

function selectBlueprint(id) {
  const select = document.getElementById('new-blueprint');
  select.value = id;
  document.querySelectorAll('.blueprint-option').forEach(option => {
    const selected = option.dataset.blueprint === id;
    option.classList.toggle('selected', selected);
    option.querySelector('input').checked = selected;
  });
  updateBlueprintSelection();
}

function updateBlueprintSelection() {
  const item = BLUEPRINTS.find(candidate => candidate.id === document.getElementById('new-blueprint').value);
  if (!item) return;
  const php = document.getElementById('new-php');
  php.innerHTML = item.php.allowed.map(version => `<option value="${esc(version)}">PHP ${esc(version)}</option>`).join('');
  php.value = item.php.default;
  document.getElementById('blueprint-note').textContent = item.description;
}

function healthClass(h) {
  if (!h) return 'gray';
  h = h.toLowerCase();
  if (h.includes('healthy')) return 'green';
  if (h.includes('unhealthy')) return 'red';
  if (h.includes('starting')) return 'yellow';
  return 'gray';
}
function statusClass(s) {
  s = (s || '').toLowerCase();
  if (s.startsWith('up')) return 'green';
  if (s.startsWith('exit') || s.startsWith('dead')) return 'red';
  return 'yellow';
}
function pctClass(p) { return p >= 90 ? 'crit' : (p >= 70 ? 'warn' : ''); }
function esc(s) { return (s || '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

function showToast(msg, isErr) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.toggle('err', !!isErr);
  t.style.display = 'block';
  clearTimeout(t._timer);
  t._timer = setTimeout(() => { t.style.display = 'none'; }, 2200);
}

async function logoutCockpit() {
  await fetch(`${BASE}/auth/logout`, { method: 'POST' });
  location.href = '/login';
}

async function loadUpdateStatus() {
  const panel = document.getElementById('update-panel');
  try {
    const response = await fetch(`${BASE}/update-status`, { cache: 'no-store' });
    const status = await response.json();
    const dot = document.getElementById('update-dot');
    if (dot) dot.hidden = !status.update_available;
    if (!panel) return;
    document.getElementById('installed-version').textContent = status.current || 'Unknown';
    document.getElementById('latest-version').textContent = status.version || 'Unavailable';
    document.getElementById('update-state').textContent = status.error
      ? 'Check failed' : status.update_available ? 'Update available'
        : status.version_status === 'ahead' ? 'Development build' : 'Up to date';
    if (status.error) {
      const error = document.getElementById('update-error');
      error.textContent = status.error;
      error.hidden = false;
      return;
    }
    const details = document.getElementById('update-details');
    details.hidden = !status.update_available;
    document.getElementById('release-name').textContent = status.name || `SpawnWP ${status.version}`;
    document.getElementById('release-notes').textContent = status.notes || 'No release notes provided.';
  } catch (error) {
    if (panel) {
      document.getElementById('update-state').textContent = 'Check failed';
      const alert = document.getElementById('update-error');
      alert.textContent = error.message;
      alert.hidden = false;
    }
  }
}

function copyUpdateCommand() {
  navigator.clipboard.writeText('sudo spawnwp update');
  showToast('Update command copied');
}

async function loadTelemetryStatus() {
  const state = document.getElementById('telemetry-state');
  if (!state) return;
  try {
    const status = await fetch(`${BASE}/telemetry`, { cache: 'no-store' }).then(response => response.json());
    state.textContent = status.enabled ? 'Enabled until consent expiry' : 'Disabled';
    document.getElementById('telemetry-disable').hidden = !status.enabled;
  } catch (error) { state.textContent = 'Unavailable'; }
}

async function disableTelemetry() {
  if (!confirm('Disable telemetry and delete the local installation identifier?')) return;
  const response = await fetch(`${BASE}/telemetry/disable`, { method: 'POST' });
  if (!response.ok) { showToast('Unable to disable telemetry', true); return; }
  showToast('Telemetry disabled');
  loadTelemetryStatus();
}

// ── Project list (rebuilds the .card-top, never the output box) ──────────────
async function loadProjects() {
  const el = document.getElementById('projects-list');
  if (!el) return;
  let projects;
  try {
    const res = await fetch(`${BASE}/projects`, { cache: 'no-store' });
    if (!res.ok) {
      el.innerHTML = `<p style="color:var(--red)">Error ${res.status} while loading.${res.status === 401 ? ' Sign in again.' : ''}</p>`;
      return;
    }
    projects = await res.json();
  } catch (e) {
    el.innerHTML = `<p style="color:var(--red)">Network error: ${e.message}.</p>`;
    return;
  }

  if (!projects.length) { el.innerHTML = '<p style="color:var(--muted)">No projects found.</p>'; return; }

  // Drop the placeholder once
  if (el.querySelector('p')) el.innerHTML = '';

  const seen = new Set();
  for (const p of projects) {
    seen.add(p.name);
    let card = document.getElementById(`card-${p.name}`);
    if (!card) {
      card = document.createElement('div');
      card.className = 'card';
      card.id = `card-${p.name}`;
      card.innerHTML = `<div class="card-top" id="top-${p.name}"></div>
        <div class="output-box" id="out-${p.name}">
          <div class="output-head"><span class="out-label">output</span><span><button class="icon-btn" onclick="copyBox('out-${p.name}')">⧉</button><button class="icon-btn" onclick="closeBox('out-${p.name}')">✕</button></span></div>
          <div class="disk-visual" id="out-${p.name}-visual"></div>
          <div class="output-body" id="out-${p.name}-body"></div>
        </div>`;
      el.appendChild(card);
    }
    const signature = JSON.stringify(p);
    if (projectSignatures[p.name] !== signature) {
      document.getElementById(`top-${p.name}`).innerHTML = renderTop(p);
      projectSignatures[p.name] = signature;
    }
    if (!(p.name in dbCache)) loadDbInfo(p.name);
    else applyDbInfo(p.name);
  }
  // Remove cards for projects that disappeared
  el.querySelectorAll('.card').forEach(c => {
    const n = c.id.replace('card-', '');
    if (!seen.has(n)) {
      c.remove();
      delete projectSignatures[n];
      delete dbCache[n];
    }
  });
}

function updateClock() {
  const clock = document.getElementById('header-clock');
  if (!clock) return;
  const now = new Date();
  clock.dateTime = now.toISOString();
  clock.textContent = now.toLocaleTimeString('en-GB', {
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  });
}

function renderTop(p) {
  const overallOk = p.containers.length > 0 && p.containers.every(c => healthClass(c.health) === 'green' || (!c.health && statusClass(c.status) === 'green'));
  const overallClass = overallOk ? 'green' : (p.containers.length === 0 ? 'gray' : 'red');
  const overallLabel = overallClass === 'green' ? 'Running' : overallClass === 'gray' ? 'Down' : 'Error';

  const rows = p.containers.map(c => {
    const cls = c.health ? healthClass(c.health) : statusClass(c.status);
    const stxt = c.health || (c.status || '').split(' ')[0] || '—';
    const id = c.container;   // full container name, key for stats
    return `<tr>
      <td class="svc-name">${esc(c.name)}</td>
      <td><span class="badge badge-${cls}" title="${esc(c.status)}"><span class="dot"></span>${esc(stxt)}</span></td>
      <td class="svc-cpu" id="cpu-${id}">·</td>
      <td class="svc-mem-cell">
        <div class="svc-mem-text" id="mem-${id}">·</div>
        <div class="metric-bar"><div class="metric-fill" id="bar-${id}"></div></div>
      </td>
      <td class="svc-actions">
        <button class="icon-btn sensitive" title="Restart ${esc(c.name)}" onclick="runAction('${p.name}','restart','${esc(c.name)}')">↺</button>
        <button class="icon-btn" title="Logs ${esc(c.name)}" onclick="runAction('${p.name}','logs','${esc(c.name)}')">▤</button>
      </td>
    </tr>`;
  }).join('');

  const table = p.containers.length ? `<table class="svc-table">
      <thead><tr><th>Service</th><th>Status</th><th>CPU</th><th>Memory</th><th></th></tr></thead>
      <tbody>${rows}</tbody></table>` : '<p class="card-meta" style="margin-top:12px">No running containers</p>';

  const urlHtml = p.url
    ? `<a href="${esc(p.url)}/" target="_blank" rel="noopener" style="color:var(--accent);text-decoration:none" title="Open the site">${esc(p.url)} ↗</a>
       &nbsp;·&nbsp;
       <a href="${esc(p.url)}/wp-admin/" target="_blank" rel="noopener" style="color:var(--accent);text-decoration:none" title="Open WordPress admin">WP Admin ↗</a>`
    : '—';

  const anyUp = p.containers.some(c => (c.status || '').toLowerCase().startsWith('up'));
  const destroyBtn = p.name === 'wp-dev' ? '' : (anyUp
    ? `<button class="btn-danger btn-sm sensitive" disabled title="Bring the site Down first to destroy it">🗑 Destroy</button>`
    : `<button class="btn-danger btn-sm sensitive" title="Permanently delete the site" onclick="destroyProject('${p.name}')">🗑 Destroy</button>`);

  return `<div class="card-header">
      <div>
        <div class="card-title">${esc(p.name)}</div>
        <div class="card-meta">${urlHtml} &nbsp;·&nbsp; PHP ${esc(p.php)} &nbsp;·&nbsp; Blueprint ${esc(p.blueprint.name)} ${esc(p.blueprint.version)} &nbsp;·&nbsp; Host port ${esc(p.port)} (local)</div>
        <div class="card-meta" id="db-${p.name}">DB …</div>
      </div>
      <span class="badge badge-${overallClass}"><span class="dot"></span>${overallLabel}</span>
    </div>
    ${table}
    <div class="actions">
      <button class="btn-success btn-sm sensitive" onclick="runAction('${p.name}','up')">▶ Up</button>
      <button class="btn-danger btn-sm sensitive" onclick="runAction('${p.name}','down')">■ Down</button>
      <button class="btn-neutral btn-sm sensitive" onclick="runAction('${p.name}','restart')">↺ Restart</button>
      <button class="btn-neutral btn-sm sensitive" onclick="runAction('${p.name}','snapshot')">💾 Snapshot</button>
      <button class="btn-neutral btn-sm" onclick="showSnapshots('${p.name}')">🕘 Restore</button>
      <button class="btn-neutral btn-sm" onclick="runAction('${p.name}','disk')">📊 Disk</button>
      <button class="btn-db btn-sm" onclick="openAdminer('${p.name}')">🗄 DB ▸</button>
      ${p.mail_url ? `<button class="btn-db btn-sm" onclick="window.open('${esc(p.mail_url)}','_blank','noopener')" title="Open Mailpit (captured mail)">✉️ Mailpit ▸</button>` : ''}
      <button class="btn-neutral btn-sm" onclick="showWpAdmin('${p.name}')">🔑 WP credentials</button>
      <select class="sensitive" onchange="if(this.value) phpSwitch('${p.name}', this.value); this.value=''">
        <option value="">PHP ${esc(p.php)} ▾</option>
        <option value="7.4">→ PHP 7.4 (legacy)</option>
        <option value="8.2">→ PHP 8.2</option>
        <option value="8.3">→ PHP 8.3</option>
        <option value="8.4">→ PHP 8.4</option>
      </select>
      ${destroyBtn}
    </div>`;
}

// ── DB info ──────────────────────────────────────────────────────────────────
async function loadDbInfo(name) {
  try {
    const res = await fetch(`${BASE}/db/${name}`, { cache: 'no-store' });
    if (res.ok) { dbCache[name] = await res.json(); applyDbInfo(name); }
  } catch (e) { /* silent */ }
}
function applyDbInfo(name) {
  const el = document.getElementById(`db-${name}`);
  if (!el) return;
  const d = dbCache[name];
  if (d && d.size_mb != null) el.textContent = `DB ${d.size_mb} MB · ${d.tables} tables`;
  else el.textContent = 'DB n/a';
}

// ── Live metrics (every 4s): host panel + per-container cpu/mem ───────────────
async function pollMetrics() {
  try {
    const requests = [fetch(`${BASE}/host`, { cache: 'no-store' })];
    if (document.body.dataset.page === 'manage') requests.push(fetch(`${BASE}/stats`, { cache: 'no-store' }));
    const [hRes, sRes] = await Promise.all(requests);
    if (hRes.ok) applyHost(await hRes.json());
    if (sRes && sRes.ok) applyStats(await sRes.json());
  } catch (e) { /* expired session or network: silent, retry on the next tick */ }
}
function applyHost(h) {
  const ram = h.ram, disk = h.disk;
  setRing('kpi-ram', ram.pct);
  setRing('kpi-disk', disk.pct);
  document.getElementById('host-ram-pct').textContent = `${Math.round(ram.pct)}%`;
  document.getElementById('host-ram-text').textContent = `${(ram.used_mb/1024).toFixed(1)} / ${(ram.total_mb/1024).toFixed(1)} GB`;
  document.getElementById('host-disk-pct').textContent = `${Math.round(disk.pct)}%`;
  document.getElementById('host-disk-text').textContent = `${disk.used_gb} / ${disk.total_gb} GB`;
  const load = h.load || [0,0,0];
  const cores = (h.status && h.status.ncpu) || 1;
  const loadPct = Math.max(0, load[0] / cores * 100);
  setRing('kpi-load', loadPct);
  document.getElementById('host-load-pct').textContent = `${Math.round(loadPct)}%`;
  document.getElementById('host-load').textContent = `${load.join(' · ')} / ${cores} cores`;
  const uh = h.uptime_h || 0;
  document.getElementById('host-uptime').textContent = uh >= 24 ? `${(uh/24).toFixed(1)} d` : `${uh} h`;

  // Guardrail: banner + disabling sensitive actions
  const st = h.status || {};
  SYS_BUSY = !!st.busy;
  document.body.classList.toggle('sys-busy', SYS_BUSY);
  if (SYS_BUSY) {
    const r = st.reason ? st.reason.charAt(0).toUpperCase() + st.reason.slice(1) : 'System under load';
    document.getElementById('sys-banner-text').textContent =
      r + ' — sensitive actions disabled to avoid instability.';
  }
}

function setRing(id, pct) {
  const el = document.getElementById(id);
  if (!el) return;
  el.style.setProperty('--ring-value', `${Math.min(Math.max(pct, 0), 100)}%`);
  el.classList.toggle('warn', pct >= 70 && pct < 90);
  el.classList.toggle('crit', pct >= 90);
}
function applyStats(stats) {
  for (const [name, s] of Object.entries(stats)) {
    const cpu = document.getElementById(`cpu-${name}`);
    const mem = document.getElementById(`mem-${name}`);
    const bar = document.getElementById(`bar-${name}`);
    if (cpu) cpu.textContent = s.cpu;
    if (mem) mem.textContent = `${s.mem_used} / ${s.mem_limit}`;
    if (bar) setBar(`bar-${name}`, parseFloat(s.mem_pct) || 0);
  }
}
function setBar(id, pct) {
  const bar = document.getElementById(id);
  if (!bar) return;
  bar.style.width = Math.min(pct, 100) + '%';
  bar.className = 'metric-fill ' + pctClass(pct);
}

// ── Output boxes ─────────────────────────────────────────────────────────────
function getOutputBox(id) {
  const box = document.getElementById(id);
  box.classList.add('visible');
  const vis = document.getElementById(id + '-visual');
  if (vis) { vis.style.display = 'none'; vis.innerHTML = ''; }
  const body = document.getElementById(id + '-body');
  body.textContent = '';
  return body;
}
function closeBox(id) { document.getElementById(id).classList.remove('visible'); }
function copyBox(id) {
  const body = document.getElementById(id + '-body');
  navigator.clipboard.writeText(body.textContent).then(() => showToast('Copied to clipboard'));
}
function appendLine(body, line, isErr) {
  const span = document.createElement('span');
  if (isErr) span.className = 'output-line-err';
  span.textContent = line + '\n';
  body.appendChild(span);
  body.scrollTop = body.scrollHeight;
}

function streamSSE(url, payload, boxId, onDone) {
  const body = getOutputBox(boxId);
  let completed = false;
  const finish = ok => {
    if (completed) return;
    completed = true;
    if (onDone) onDone(ok);
  };
  fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).then(async res => {
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      appendLine(body, '❌ ' + (err.detail || res.statusText), true);
      finish(false);
      return;
    }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const parts = buf.split('\n\n');
      buf = parts.pop();
      for (const part of parts) {
        const match = part.match(/^data: (.+)$/m);
        if (!match) continue;
        const line = JSON.parse(match[1]);
        if (line.startsWith('__EXIT__')) {
          const rc = parseInt(line.replace('__EXIT__', ''));
          appendLine(body, rc === 0 ? '✅ Done.' : `❌ Exited with code ${rc}`, rc !== 0);
          finish(rc === 0);
          return;
        }
        appendLine(body, line, line.toLowerCase().includes('error'));
      }
    }
    appendLine(body, '⚠️ Connection closed before the final status. Refreshing state.', true);
    finish(false);
  }).catch(e => {
    appendLine(body, '❌ ' + e.message, true);
    finish(false);
  });
}

function monitorProjectRefresh() {
  loadProjects();
  const timer = setInterval(loadProjects, 1500);
  let stopped = false;
  return () => {
    if (stopped) return;
    stopped = true;
    clearInterval(timer);
    loadProjects();
    setTimeout(loadProjects, 750);
  };
}

function runAction(project, action, service) {
  if (!['logs', 'disk'].includes(action) && blockedIfBusy()) return;
  const payload = { project, action };
  if (service) payload.service = service;
  const lifecycle = ['up', 'down', 'restart'].includes(action);
  const stopRefresh = lifecycle ? monitorProjectRefresh() : null;
  streamSSE(`${BASE}/run`, payload, `out-${project}`, ok => {
    if (stopRefresh) stopRefresh();
  });
  if (action === 'disk') loadDiskVisual(project);
}

// ── Disk visual: real site footprint + host context ──────────────────────────
const DV_COLORS = ['#f6b269', '#f8f8f8', '#a1a1aa', '#5c5c63', '#2b2b30'];
function fmtMB(x) { return x >= 1000 ? (x / 1000).toFixed(2) + ' GB' : x + ' MB'; }

async function loadDiskVisual(project) {
  const vis = document.getElementById(`out-${project}-visual`);
  if (!vis) return;
  vis.style.display = 'block';
  vis.innerHTML = '<span style="color:var(--muted);font-size:12px">Analyzing space…</span>';
  try {
    const res = await fetch(`${BASE}/disk/${project}`, { cache: 'no-store' });
    if (!res.ok) { vis.style.display = 'none'; return; }
    renderDiskVisual(vis, await res.json());
  } catch (e) { vis.style.display = 'none'; }
}
function renderDiskVisual(vis, d) {
  const label = n => n === 'db_data' ? 'DB volume' : n === 'wp_data' ? 'WordPress volume' : 'Volume ' + n;
  const contSum = Math.round((d.containers || []).reduce((a, c) => a + c.mb, 0) * 100) / 100;
  // Site footprint components
  const comps = (d.volumes || []).map(v => ({ label: label(v.name), mb: v.mb }));
  comps.push({ label: 'wp-content files (host)', mb: d.content_mb });
  comps.push({ label: 'Containers (writable layer)', mb: contSum });
  const total = d.total_mb || comps.reduce((a, c) => a + c.mb, 0) || 1;

  const segs = comps.map((c, i) =>
    `<div class="disk-seg" style="width:${(c.mb / total * 100)}%;background:${DV_COLORS[i % DV_COLORS.length]}" title="${esc(c.label)}: ${fmtMB(c.mb)}"></div>`
  ).join('');
  const legend = comps.map((c, i) =>
    `<span><span class="swatch" style="background:${DV_COLORS[i % DV_COLORS.length]}"></span>${esc(c.label)} <b>${fmtMB(c.mb)}</b></span>`
  ).join('');

  // Per-container detail
  const contDetail = (d.containers || [])
    .map(c => `${esc(c.name)} ${c.mb} MB`).join('  ·  ');

  // Host context
  const h = d.host, cls = pctClass(h.pct);
  const hc = cls === 'crit' ? 'var(--red)' : cls === 'warn' ? 'var(--yellow)' : 'var(--green)';

  vis.innerHTML = `
    <div class="dv-title">Site footprint “${esc(d.project)}” — <b style="color:var(--text)">${fmtMB(total)}</b></div>
    <div class="disk-stack">${segs}</div>
    <div class="disk-legend">${legend}</div>
    <div class="disk-docker">
      <div class="row"><span>Per-container layer</span><span style="color:var(--muted)">${contDetail || '—'}</span></div>
    </div>
    <div class="dv-title" style="margin-top:12px">Host disk /</div>
    <div class="disk-stack">
      <div class="disk-seg" style="width:${h.pct}%;background:${hc}"></div>
      <div class="disk-seg" style="width:${100 - h.pct}%;background:#334155"></div>
    </div>
    <div class="disk-legend">
      <span><span class="swatch" style="background:${hc}"></span>Used <b>${h.used_gb} GB</b> (${h.pct}%)</span>
      <span><span class="swatch" style="background:#334155"></span>Free <b>${h.free_gb} GB</b></span>
      <span>Total <b>${h.total_gb} GB</b></span>
    </div>`;
}

function phpSwitch(project, version) {
  if (blockedIfBusy()) return;
  streamSSE(`${BASE}/php-switch`, { project, version }, `out-${project}`, ok => {
    if (ok) setTimeout(loadProjects, 2000);
  });
}

function createProject(e) {
  e.preventDefault();
  if (blockedIfBusy()) return;
  const name = document.getElementById('new-name').value.trim();
  const blueprint = document.getElementById('new-blueprint').value;
  const php_version = document.getElementById('new-php').value;
  if (!name) return;
  const btn = document.getElementById('btn-create');
  const result = document.getElementById('deploy-result');
  btn.disabled = true;
  DEPLOY_ACTIVE = true;
  result.hidden = true;
  streamSSE(`${BASE}/new-project`, { name, blueprint, php_version }, 'out-new', ok => {
    btn.disabled = false;
    DEPLOY_ACTIVE = false;
    if (ok) {
      const url = `${PLATFORM.sites_url || ''}/${encodeURIComponent(name)}`;
      result.innerHTML = `<strong>Environment ready.</strong><div><a href="${url}/" target="_blank" rel="noopener">Open site ↗</a><a href="${url}/wp-admin/" target="_blank" rel="noopener">WP Admin ↗</a><a href="/manage">Manage environment →</a></div>`;
      result.hidden = false;
    }
  });
}

// ── Adminer (auto-login via bridge page served by the cockpit) ───────────────
function openAdminer(name) {
  window.open(`${BASE}/db/${name}/login`, '_blank', 'noopener');
}

// ── WordPress admin credentials (read from the site's .env) ───────────────────
async function showWpAdmin(name) {
  const body = getOutputBox(`out-${name}`);
  appendLine(body, '🔑 Fetching WordPress admin credentials…');
  try {
    const res = await fetch(`${BASE}/wp/${name}/admin`, { cache: 'no-store' });
    if (!res.ok) {
      appendLine(body, `❌ Error ${res.status}` + (res.status === 401 ? ' — sign in again' : ''), true);
      return;
    }
    const d = await res.json();
    body.textContent = '';
    appendLine(body, `URL:      ${d.url}`);
    appendLine(body, `User:     ${d.user}`);
    appendLine(body, `Password: ${d.password}`);
    if (d.email) appendLine(body, `Email:    ${d.email}`);
    appendLine(body, '');
    appendLine(body, '⧉ top-right copies everything · 🔓 copy only the password ↓');
    // button to copy only the password
    const btn = document.createElement('button');
    btn.className = 'icon-btn';
    btn.textContent = '🔓 Copy password';
    btn.style.marginTop = '8px';
    btn.onclick = () => navigator.clipboard.writeText(d.password)
      .then(() => showToast('Password copied to clipboard'));
    body.appendChild(btn);
  } catch (e) {
    appendLine(body, '❌ ' + e.message, true);
  }
}

// ── Snapshot / Restore ───────────────────────────────────────────────────────
function fmtSnapTs(ts) {
  const m = ts.match(/^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})$/);
  return m ? `${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}:${m[6]}` : ts;
}
function fmtKB(kb) { return kb >= 1024 ? (kb / 1024).toFixed(1) + ' MB' : kb + ' KB'; }

async function showSnapshots(name) {
  const body = getOutputBox(`out-${name}`);
  appendLine(body, '🕘 Loading snapshots…');
  try {
    const res = await fetch(`${BASE}/snapshots/${name}`, { cache: 'no-store' });
    if (!res.ok) { appendLine(body, `❌ Error ${res.status}` + (res.status === 401 ? ' — sign in again' : ''), true); return; }
    renderSnapshots(name, body, await res.json());
  } catch (e) { appendLine(body, '❌ ' + e.message, true); }
}

function renderSnapshots(name, body, snaps) {
  body.textContent = '';
  if (!snaps.length) {
    appendLine(body, 'No snapshots: create one with 💾 Snapshot (saves DB + uploads).');
    return;
  }
  const head = document.createElement('div');
  head.style.cssText = 'margin-bottom:8px;color:var(--muted)';
  head.textContent = `${snaps.length} snapshot(s) — “Restore” overwrites the site's current state:`;
  body.appendChild(head);
  for (const s of snaps) {
    const row = document.createElement('div');
    row.style.cssText = 'display:flex;align-items:center;gap:10px;padding:5px 0;border-top:1px solid var(--surface2)';
    const info = document.createElement('span');
    info.style.cssText = 'flex:1;font-family:monospace;font-size:12px';
    info.innerHTML = `${fmtSnapTs(s.name)} <span style="color:var(--muted)">· DB ${fmtKB(s.db_kb)}${s.has_files ? ' · 📦 ' + fmtKB(s.files_kb) : ''}</span>`;
    const btn = document.createElement('button');
    btn.className = 'btn-danger btn-sm sensitive';
    btn.textContent = '↩ Restore';
    btn.onclick = () => doRestore(name, s.name, s.has_files);
    row.appendChild(info);
    row.appendChild(btn);
    body.appendChild(row);
  }
}

function doRestore(name, snapshot, hasFiles) {
  if (blockedIfBusy()) return;
  const what = hasFiles ? 'the DATABASE and UPLOADS' : 'the DATABASE';
  if (!confirm(`⚠️ RESTORE snapshot ${snapshot}\n\nThis will overwrite ${what} of site "${name}", rolling it back to that point.\nAny later changes will be lost.\n\nProceed?`)) return;
  streamSSE(`${BASE}/restore`, { project: name, snapshot }, `out-${name}`, ok => {
    if (ok) { showToast(`Snapshot ${snapshot} restored`); setTimeout(() => loadProjects(true), 1500); }
  });
}

// ── Site destruction (irreversible, double confirm; only when site is Down) ──
function destroyProject(name) {
  if (blockedIfBusy()) return;
  const warn = `⚠️ IRREVERSIBLE DESTRUCTION\n\n`
    + `You are about to COMPLETELY delete site "${name}":\n`
    + `  • Docker containers and volumes (database + all files)\n`
    + `  • directory /srv/${name}\n`
    + `  • the site's Nginx block\n\n`
    + `This operation is NOT reversible and cannot be undone.\n`
    + `Press OK to continue.`;
  if (!confirm(warn)) return;
  const typed = prompt(`Final confirmation: type the site name “${name}” exactly to destroy it.`);
  if (typed === null) return;
  if (typed.trim() !== name) { showToast('Name mismatch: destruction cancelled', true); return; }
  const stopRefresh = monitorProjectRefresh();
  streamSSE(`${BASE}/destroy`, { name, confirm: name }, `out-${name}`, ok => {
    stopRefresh();
    if (ok) showToast(`Site "${name}" destroyed`);
  });
}

// ── Boot ─────────────────────────────────────────────────────────────────────
updateClock();
loadPlatform();
if (document.body.dataset.page === 'deploy') loadBlueprints();
if (document.body.dataset.page === 'manage') loadProjects();
if (document.body.dataset.page !== 'updates') pollMetrics();
loadUpdateStatus();
loadTelemetryStatus();
setInterval(updateClock, 1000);
if (document.body.dataset.page === 'manage') setInterval(loadProjects, 30000);
if (document.body.dataset.page !== 'updates') setInterval(pollMetrics, 4000);
window.addEventListener('beforeunload', event => {
  if (!DEPLOY_ACTIVE) return;
  event.preventDefault();
  event.returnValue = '';
});

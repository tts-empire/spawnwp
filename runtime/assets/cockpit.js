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
let UPDATE_RUNNING = false;
let REAUTH_PROMISE = null;
const PHP_SWITCH_ACTIVE = new Set();

function webauthnB64(value) {
  const encoded = btoa(String.fromCharCode(...new Uint8Array(value)));
  return encoded.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}
function webauthnBytes(value) {
  value = value.replace(/-/g, '+').replace(/_/g, '/');
  return Uint8Array.from(atob(value), character => character.charCodeAt(0));
}
function preparePublicKey(options) {
  options.challenge = webauthnBytes(options.challenge);
  if (options.allowCredentials) options.allowCredentials.forEach(item => { item.id = webauthnBytes(item.id); });
  return options;
}
function serializeCredential(value) {
  return {
    id: value.id, rawId: webauthnB64(value.rawId), type: value.type,
    authenticatorAttachment: value.authenticatorAttachment,
    clientExtensionResults: value.getClientExtensionResults(),
    response: {
      clientDataJSON: webauthnB64(value.response.clientDataJSON),
      authenticatorData: webauthnB64(value.response.authenticatorData),
      signature: webauthnB64(value.response.signature),
      userHandle: value.response.userHandle ? webauthnB64(value.response.userHandle) : null,
    },
  };
}

function reauthDialog() {
  let dialog = document.getElementById('reauth-dialog');
  if (dialog) return dialog;
  dialog = document.createElement('dialog');
  dialog.id = 'reauth-dialog';
  dialog.innerHTML = `<div class="reauth-card"><div class="section-title">Security confirmation</div><h2>Confirm your identity</h2><p>This sensitive action requires a recent sign-in. Confirm with your Passkey; the action will resume automatically.</p><p class="reauth-error" id="reauth-error" aria-live="polite"></p><div class="reauth-actions"><button class="btn-primary" id="reauth-passkey" type="button">Confirm with Passkey</button><button class="btn-neutral" id="reauth-cancel" type="button">Cancel</button></div><button class="reauth-login" id="reauth-login" type="button">Sign out and use password + authenticator</button></div>`;
  document.body.appendChild(dialog);
  return dialog;
}

function requestRecentAuthentication() {
  if (REAUTH_PROMISE) return REAUTH_PROMISE;
  REAUTH_PROMISE = new Promise((resolve, reject) => {
    const dialog = reauthDialog();
    const passkey = document.getElementById('reauth-passkey');
    const error = document.getElementById('reauth-error');
    const finish = failure => {
      dialog.close();
      REAUTH_PROMISE = null;
      failure ? reject(failure) : resolve();
    };
    error.textContent = '';
    passkey.disabled = false;
    passkey.textContent = 'Confirm with Passkey';
    document.getElementById('reauth-cancel').onclick = () => finish(new Error('Action cancelled'));
    document.getElementById('reauth-login').onclick = async () => { await logoutCockpit(); };
    passkey.onclick = async () => {
      if (!window.PublicKeyCredential) {
        error.textContent = 'This browser cannot use Passkeys. Sign out and use password + authenticator.';
        return;
      }
      passkey.disabled = true;
      passkey.textContent = 'Waiting for Passkey…';
      try {
        const startResponse = await fetch(`${BASE}/auth/reauth/start`, { method: 'POST' });
        const start = await startResponse.json();
        if (!startResponse.ok) throw new Error(start.detail || 'Unable to start identity confirmation');
        const credential = await navigator.credentials.get({ publicKey: preparePublicKey(start.publicKey) });
        if (!credential) throw new Error('Passkey confirmation was cancelled');
        const endResponse = await fetch(`${BASE}/auth/reauth/finish`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ceremony: start.ceremony, credential: serializeCredential(credential) }),
        });
        const end = await endResponse.json();
        if (!endResponse.ok) throw new Error(end.detail || 'Identity confirmation failed');
        finish();
      } catch (failure) {
        error.textContent = failure.message || String(failure);
        passkey.disabled = false;
        passkey.textContent = 'Try Passkey again';
      }
    };
    dialog.oncancel = event => { event.preventDefault(); finish(new Error('Action cancelled')); };
    dialog.showModal();
  });
  return REAUTH_PROMISE;
}

async function sensitiveFetch(url, options) {
  let response = await fetch(url, options);
  if (response.status !== 403) return response;
  const payload = await response.clone().json().catch(() => ({}));
  if (!String(payload.detail || '').startsWith('Recent authentication required')) return response;
  await requestRecentAuthentication();
  return fetch(url, options);
}

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
  e.target.classList.remove('input-error');
  const help = document.getElementById('new-name-help');
  help.classList.remove('input-error');
  help.textContent = 'Lowercase letters, numbers and hyphens only; no spaces. Maximum 31 characters.';
});

// Per-blueprint deploy time on a ready image; development adds wp.org plugin installs.
const BLUEPRINT_TIMES = { clean: '~35 sec', demo: '~35 sec', development: '~1–2 min' };
let PHP_IMAGES = null;   // PHP versions whose image is already built; null = unknown

function blueprintTime(id) { return BLUEPRINT_TIMES[id] || '~35 sec'; }

async function loadBlueprints() {
  const select = document.getElementById('new-blueprint');
  const button = document.getElementById('btn-create');
  const catalog = document.getElementById('blueprint-catalog');
  if (!select || !catalog) return;
  // Built images are only needed for the expected-time estimate: never block on them.
  fetch(`${BASE}/images`, { cache: 'no-store' }).then(r => r.json())
    .then(data => { PHP_IMAGES = (data.images || []).map(img => img.php_version); updateExpectedTime(); })
    .catch(() => {});
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
      <div class="blueprint-time"><span class="badge badge-green">${item.id === 'development' ? '⏱' : '⚡'} ${esc(blueprintTime(item.id))}</span>${item.id === 'development' ? '<small class="field-help" style="margin:0">installs plugins from wp.org</small>' : ''}</div>
      <div class="blueprint-facts"><span>${esc(item.source)}</span><span>${item.debug ? 'debug on' : 'debug off'}</span><span>${esc(item.content_preset)}</span><span>PHP ${item.php.allowed.map(version => version === '7.4' ? '7.4 legacy' : esc(version)).join(', ')}</span></div>
    </label>`).join('');
    catalog.querySelectorAll('.blueprint-option').forEach(option => option.addEventListener('click', () => selectBlueprint(option.dataset.blueprint)));
    document.getElementById('new-php').addEventListener('change', updateExpectedTime);
    document.getElementById('new-lifetime').addEventListener('change', updateExpectedTime);
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
  updateExpectedTime();
}

// The launch bar's live estimate: the real number for the CURRENT choice, so the
// "first time is slow" warning only appears when it is actually true.
function updateExpectedTime() {
  const timeEl = document.getElementById('expected-time');
  const noteEl = document.getElementById('expected-time-note');
  if (!timeEl) return;
  const blueprintId = document.getElementById('new-blueprint').value;
  const version = document.getElementById('new-php').value;
  const base = blueprintTime(blueprintId);
  const lifetime = parseInt(document.getElementById('new-lifetime').value, 10) || 0;
  const expiry = lifetime ? ` · expires after ${lifetime} day${lifetime === 1 ? '' : 's'}` : '';
  if (PHP_IMAGES === null || !version) {
    timeEl.textContent = base;
    timeEl.className = '';
    noteEl.textContent = expiry.replace(' · ', '');
    return;
  }
  if (PHP_IMAGES.includes(version)) {
    timeEl.textContent = base;
    timeEl.className = 'time-fast';
    noteEl.textContent = `PHP ${version} image ready${expiry}`;
  } else {
    timeEl.textContent = '~5 min';
    timeEl.className = 'time-slow';
    noteEl.textContent = `one-off first build for PHP ${version}, then ${base}${expiry}`;
  }
}

function togglePhpPanel() {
  const head = document.getElementById('php-panel-toggle');
  const body = document.getElementById('php-panel-body');
  const open = head.getAttribute('aria-expanded') === 'true';
  head.setAttribute('aria-expanded', String(!open));
  body.hidden = open;
}

function phpFormChanged() {
  const chip = document.getElementById('php-modified');
  const s = collectPhpSettings(true);
  const changed = s === null ? 0
    : Object.keys(PHP_DEFAULTS).filter(k => s[k] !== PHP_DEFAULTS[k]).length;
  chip.hidden = changed === 0;
  chip.textContent = `${changed} custom value${changed === 1 ? '' : 's'}`;
}

function resetPhpForm() {
  document.getElementById('php-memory').value = PHP_DEFAULTS.memory_limit;
  document.getElementById('php-upload').value = PHP_DEFAULTS.upload_max_filesize;
  document.getElementById('php-post').value = PHP_DEFAULTS.post_max_size;
  document.getElementById('php-exec').value = PHP_DEFAULTS.max_execution_time;
  document.getElementById('php-vars').value = PHP_DEFAULTS.max_input_vars;
  document.getElementById('php-input-time').value = PHP_DEFAULTS.max_input_time;
  document.getElementById('php-display-errors').checked = PHP_DEFAULTS.display_errors;
  document.getElementById('php-modified').hidden = true;
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

async function applyDashboardUpdate() {
  if (UPDATE_RUNNING) return;
  const current = document.getElementById('installed-version').textContent;
  const latest = document.getElementById('latest-version').textContent;
  if (!confirm(`Install SpawnWP ${latest}?\n\nThe cockpit will restart during the update. Existing WordPress environments keep running.`)) return;
  const button = document.getElementById('apply-update');
  const progress = document.getElementById('update-progress');
  UPDATE_RUNNING = true;
  button.disabled = true;
  progress.textContent = 'Starting signed update…';
  document.getElementById('update-state').textContent = 'Updating';
  try {
    const response = await sensitiveFetch(`${BASE}/update/apply`, { method: 'POST' });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
    monitorDashboardUpdate(current, 0);
  } catch (error) {
    UPDATE_RUNNING = false;
    button.disabled = false;
    progress.textContent = error.message;
    showToast(error.message, true);
  }
}

async function monitorDashboardUpdate(previousVersion, attempt) {
  const progress = document.getElementById('update-progress');
  try {
    const version = await fetch(`${BASE}/version`, { cache: 'no-store' }).then(response => response.json());
    if (version.version && version.version !== previousVersion) {
      progress.textContent = `Updated to ${version.version}. Reloading…`;
      document.getElementById('update-state').textContent = 'Up to date';
      setTimeout(() => location.reload(), 1500);
      return;
    }
    const job = await fetch(`${BASE}/update/job`, { cache: 'no-store' }).then(response => response.json());
    if (job.state === 'failed' || job.exit_code) throw new Error(job.error || 'Update failed');
    progress.textContent = job.state === 'active' ? 'Installing and verifying the release…' : 'Waiting for the updater…';
  } catch (error) {
    if (attempt > 8 && !String(error.message).includes('fetch')) {
      UPDATE_RUNNING = false;
      document.getElementById('apply-update').disabled = false;
      progress.textContent = error.message;
      showToast(error.message, true);
      return;
    }
    progress.textContent = 'Cockpit is restarting; reconnecting…';
  }
  if (attempt >= 120) {
    UPDATE_RUNNING = false;
    document.getElementById('apply-update').disabled = false;
    progress.textContent = 'Update timed out. Check: sudo systemctl status spawnwp-update';
    return;
  }
  setTimeout(() => monitorDashboardUpdate(previousVersion, attempt + 1), 2000);
}

async function loadTelemetryStatus() {
  const state = document.getElementById('telemetry-state');
  const toggle = document.getElementById('telemetry-toggle');
  if (!state) return;
  try {
    const status = await fetch(`${BASE}/telemetry`, { cache: 'no-store' }).then(response => response.json());
    state.textContent = status.enabled && status.expires_at
      ? `Enabled until ${new Date(status.expires_at * 1000).toLocaleDateString()}` : 'Disabled';
    toggle.checked = !!status.enabled;
    toggle.disabled = false;
  } catch (error) {
    state.textContent = 'Unavailable';
    toggle.disabled = true;
  }
}

async function changeTelemetry(toggle) {
  toggle.disabled = true;
  const enabling = toggle.checked;
  if (enabling) {
    const accepted = confirm('Help us improve SpawnWP\n\nShare anonymous aggregate data once a week for 90 days: SpawnWP version, OS, architecture, enabled feature flags and current environment count.\n\nWe do not collect domains, IP addresses, site names, content, credentials or logs. You can disable this at any time.\n\nEnable telemetry?');
    if (!accepted) { toggle.checked = false; toggle.disabled = false; return; }
  } else if (!confirm('Disable telemetry and delete the local installation identifier?')) {
    toggle.checked = true;
    toggle.disabled = false;
    return;
  }
  try {
    const response = await fetch(`${BASE}/telemetry/${enabling ? 'enable' : 'disable'}`, { method: 'POST' });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || 'Request failed');
    showToast(enabling ? 'Telemetry enabled for 90 days' : 'Telemetry disabled');
  } catch (error) {
    showToast(`Unable to ${enabling ? 'enable' : 'disable'} telemetry: ${error.message}`, true);
  } finally {
    await loadTelemetryStatus();
  }
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
      <span class="card-badges">${expiryBadge(p)}<span class="badge badge-${overallClass}"><span class="dot"></span>${overallLabel}</span></span>
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
      <button class="btn-neutral btn-sm" onclick="showPhpIni('${p.name}')" title="memory_limit, upload sizes, execution time…">⚙️ PHP settings</button>
      ${p.expires_at ? `<button class="btn-neutral btn-sm" onclick="showExpiry('${p.name}', ${p.days_left})" title="Extend the lifetime or make the site permanent">⏳ Lifetime</button>` : ''}
      <select class="sensitive" ${PHP_SWITCH_ACTIVE.has(p.name) ? 'disabled title="PHP switch in progress"' : ''} onchange="if(this.value) phpSwitch('${p.name}', this.value); this.value=''">
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
  body.style.display = '';
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

function streamSSE(url, payload, boxId, onDone, options = {}) {
  const body = getOutputBox(boxId);
  let completed = false;
  const finish = ok => {
    if (completed) return;
    completed = true;
    if (onDone) onDone(ok);
  };
  sensitiveFetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).then(async res => {
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      if (options.onError) options.onError(new Error(err.detail || res.statusText), body);
      else appendLine(body, '❌ ' + (err.detail || res.statusText), true);
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
        if (line && typeof line === 'object') {
          if (options.onEvent) options.onEvent(line, body);
          continue;
        }
        if (line.startsWith('__EXIT__')) {
          const rc = parseInt(line.replace('__EXIT__', ''));
          if (options.onExit) options.onExit(rc, body);
          else appendLine(body, rc === 0 ? '✅ Done.' : `❌ Exited with code ${rc}`, rc !== 0);
          finish(rc === 0);
          return;
        }
        appendLine(body, line, line.toLowerCase().includes('error'));
        if (options.onLine) options.onLine(line, body);
      }
    }
    appendLine(body, '⚠️ Connection closed before the final status. Refreshing state.', true);
    finish(false);
  }).catch(e => {
    if (options.onError) options.onError(e, body);
    else appendLine(body, '❌ ' + e.message, true);
    finish(false);
  });
}

function initPhpProgress(project, target) {
  const box = document.getElementById(`out-${project}`);
  const visual = document.getElementById(`out-${project}-visual`);
  const body = document.getElementById(`out-${project}-body`);
  box.querySelector('.out-label').textContent = `PHP switch → ${target}`;
  visual.style.display = 'block';
  visual.className = 'disk-visual php-progress';
  visual.innerHTML = `<div class="php-progress-head"><div><strong>Preparing PHP ${esc(target)}</strong><span id="php-phase-${project}">Checking image cache…</span></div><b id="php-percent-${project}">0%</b></div><div class="php-progress-track"><div id="php-bar-${project}"></div></div><div class="php-first-notice" id="php-notice-${project}" hidden></div><button class="php-details" type="button">Show technical details</button>`;
  body.style.display = 'none';
  visual.querySelector('.php-details').onclick = event => {
    const opening = body.style.display === 'none';
    body.style.display = opening ? 'block' : 'none';
    event.target.textContent = opening ? 'Hide technical details' : 'Show technical details';
  };
}

function phpProgressEvent(project, event, body) {
  const phase = document.getElementById(`php-phase-${project}`);
  const percent = document.getElementById(`php-percent-${project}`);
  const bar = document.getElementById(`php-bar-${project}`);
  const notice = document.getElementById(`php-notice-${project}`);
  if (event.type === 'log') {
    appendLine(body, event.line || '');
    while (body.childElementCount > 500) body.firstElementChild.remove();
    return;
  }
  if (event.type === 'start') {
    if (event.first_download) {
      notice.hidden = false;
      notice.textContent = `First use of PHP ${event.target}: downloading and compiling the image may take several minutes.`;
    } else {
      notice.hidden = false;
      notice.classList.add('cached');
      notice.textContent = `PHP ${event.target} is already cached. This switch should be quick.`;
    }
    phase.textContent = `PHP ${event.previous} → PHP ${event.target}`;
    return;
  }
  if (event.type === 'progress') {
    phase.textContent = event.message || event.phase || 'Working…';
    bar.classList.toggle('indeterminate', !!event.indeterminate);
    if (event.percent != null) {
      bar.style.width = `${Math.max(0, Math.min(100, event.percent))}%`;
      percent.textContent = `${event.percent}%`;
    } else {
      percent.textContent = 'Working';
    }
    return;
  }
  if (event.type === 'complete') {
    phase.textContent = event.message || 'PHP switch complete';
    percent.textContent = '100%';
    bar.classList.remove('indeterminate', 'failed');
    bar.classList.add('complete');
    bar.style.width = '100%';
    return;
  }
  if (event.type === 'error') {
    phase.textContent = event.message || 'PHP switch failed';
    percent.textContent = 'Failed';
    bar.classList.remove('indeterminate');
    bar.classList.add('failed');
    appendLine(body, `ERROR: ${event.message || 'PHP switch failed'}`, true);
  }
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
  if (PHP_SWITCH_ACTIVE.has(project)) {
    showToast('A PHP switch is already running for this environment', true);
    return;
  }
  PHP_SWITCH_ACTIVE.add(project);
  delete projectSignatures[project];
  loadProjects();
  streamSSE(`${BASE}/php-switch`, { project, version }, `out-${project}`, ok => {
    PHP_SWITCH_ACTIVE.delete(project);
    delete projectSignatures[project];
    setTimeout(loadProjects, ok ? 1500 : 0);
  }, {
    onEvent: (event, body) => phpProgressEvent(project, event, body),
    onExit: (code, body) => {
      if (code !== 0 && !body.querySelector('.output-line-err')) {
        appendLine(body, `Process exited with code ${code}`, true);
      }
    },
    onError: (error, body) => phpProgressEvent(project, { type: 'error', message: error.message }, body),
  });
  initPhpProgress(project, version);
}

function createProject(e) {
  e.preventDefault();
  if (blockedIfBusy()) return;
  const nameInput = document.getElementById('new-name');
  const nameHelp = document.getElementById('new-name-help');
  const name = nameInput.value.trim();
  const blueprint = document.getElementById('new-blueprint').value;
  const php_version = document.getElementById('new-php').value;
  const validName = /^[a-z0-9][a-z0-9-]{0,30}$/.test(name);
  nameInput.classList.toggle('input-error', !validName);
  nameHelp.classList.toggle('input-error', !validName);
  if (!validName) {
    nameHelp.textContent = name.includes(' ')
      ? 'Spaces are not allowed in site URLs. Use lowercase letters, numbers and hyphens, for example: primo-test.'
      : 'Use lowercase letters, numbers and hyphens only. Start with a letter or number; maximum 31 characters.';
    nameInput.focus();
    return;
  }
  nameHelp.textContent = 'Lowercase letters, numbers and hyphens only; no spaces. Maximum 31 characters.';
  const btn = document.getElementById('btn-create');
  const result = document.getElementById('deploy-result');
  const notice = document.getElementById('deploy-notice');
  btn.disabled = true;
  DEPLOY_ACTIVE = true;
  result.hidden = true;
  notice.hidden = true;
  notice.classList.remove('cached');
  const php_settings = collectPhpSettings();
  if (php_settings === undefined) return;   // invalid input, message already shown
  const lifetime_days = parseInt(document.getElementById('new-lifetime').value, 10) || 0;
  streamSSE(`${BASE}/new-project`, { name, blueprint, php_version, php_settings, lifetime_days }, 'out-new', ok => {
    btn.disabled = false;
    DEPLOY_ACTIVE = false;
    if (ok) {
      const url = `${PLATFORM.sites_url || ''}/${encodeURIComponent(name)}`;
      result.innerHTML = `<strong>Environment ready.</strong><div><a href="${url}/" target="_blank" rel="noopener">Open site ↗</a><a href="${url}/wp-admin/" target="_blank" rel="noopener">WP Admin ↗</a><a href="/manage">Manage environment →</a></div>`;
      result.hidden = false;
    }
  }, {
    // Surface the image-build decision streamed by new-project.sh: the one-off
    // first build per PHP version takes minutes, every later deploy does not.
    onLine: line => {
      const first = line.match(/first use of PHP ([\d.]+)/);
      if (first) {
        notice.hidden = false;
        notice.textContent = `⏳ First deploy on PHP ${first[1]}: its image is being downloaded and built — a one-off step of about 5 minutes. Every later site on PHP ${first[1]} deploys in about 35 seconds.`;
      } else if (/build context changed|forcing a fresh php image/.test(line)) {
        notice.hidden = false;
        notice.textContent = '⏳ The PHP image needs a rebuild (SpawnWP update or forced): this deploy takes about 5 minutes; the next ones about 35 seconds.';
      } else if (/Reusing php image .* \(stale: (\d+) days old\)/.test(line)) {
        const days = line.match(/stale: (\d+) days old/)[1];
        notice.hidden = false;
        notice.textContent = `⚠️ Deploying in about 35 seconds, but this PHP image is ${days} days old — the WordPress inside may be outdated. You can refresh it from the System info tab.`;
      } else if (/Reusing php image/.test(line)) {
        notice.hidden = false;
        notice.classList.add('cached');
        notice.textContent = '⚡ PHP image already built — this deploy should take about 35 seconds.';
      }
    },
  });
}

// ── Per-site PHP settings (deploy form + manage editing) ─────────────────────
const PHP_DEFAULTS = { memory_limit: '256M', upload_max_filesize: '64M', post_max_size: '64M',
  max_execution_time: 120, max_input_vars: 3000, max_input_time: -1, display_errors: false };
const PHP_SIZE_RE = /^[0-9]{1,4}[KMG]$/;

// Reads the deploy form's advanced section. Returns null when everything is at
// the defaults (nothing to send), undefined when a value is invalid (a toast is
// shown unless silent — silent callers just want the modified/unmodified state).
function collectPhpSettings(silent = false) {
  const val = id => document.getElementById(id).value.trim().toUpperCase();
  const num = id => parseInt(document.getElementById(id).value, 10);
  const s = {
    memory_limit: val('php-memory') || '256M',
    upload_max_filesize: val('php-upload') || '64M',
    post_max_size: val('php-post') || '64M',
    max_execution_time: isNaN(num('php-exec')) ? 120 : num('php-exec'),
    max_input_vars: isNaN(num('php-vars')) ? 3000 : num('php-vars'),
    max_input_time: isNaN(num('php-input-time')) ? -1 : num('php-input-time'),
    display_errors: document.getElementById('php-display-errors').checked,
  };
  for (const field of ['memory_limit', 'upload_max_filesize', 'post_max_size']) {
    if (!PHP_SIZE_RE.test(s[field])) {
      if (silent) return s;
      showToast(`Invalid ${field}: use a number with K/M/G unit, e.g. 128M`, true);
      return undefined;
    }
  }
  const unchanged = Object.keys(PHP_DEFAULTS).every(k => s[k] === PHP_DEFAULTS[k]);
  return unchanged ? null : s;
}

async function showPhpIni(name) {
  const body = getOutputBox(`out-${name}`);
  appendLine(body, '⚙️ Loading PHP settings…');
  try {
    const data = await fetch(`${BASE}/php-ini/${encodeURIComponent(name)}`, { cache: 'no-store' }).then(r => r.json());
    body.textContent = '';
    if (!data.supported) {
      appendLine(body, `PHP settings are not available for "${name}": the site was created before SpawnWP 0.3.14. Recreate it to use them.`, true);
      return;
    }
    const s = data.settings;
    const form = document.createElement('div');
    form.className = 'php-advanced-grid php-inline-form';
    form.innerHTML = `
      <label><span>memory_limit</span><input id="pi-memory-${name}" type="text" value="${esc(s.memory_limit)}"></label>
      <label><span>upload_max_filesize</span><input id="pi-upload-${name}" type="text" value="${esc(s.upload_max_filesize)}"></label>
      <label><span>post_max_size</span><input id="pi-post-${name}" type="text" value="${esc(s.post_max_size)}"></label>
      <label><span>max_execution_time</span><input id="pi-exec-${name}" type="number" min="10" max="3600" value="${s.max_execution_time}"></label>
      <label><span>max_input_vars</span><input id="pi-vars-${name}" type="number" min="100" max="100000" value="${s.max_input_vars}"></label>
      <label><span>max_input_time</span><input id="pi-time-${name}" type="number" min="-1" max="3600" value="${s.max_input_time}"></label>
      <label class="php-advanced-check"><input id="pi-errors-${name}" type="checkbox" ${s.display_errors ? 'checked' : ''}><span>display_errors</span></label>`;
    const apply = document.createElement('button');
    apply.className = 'icon-btn sensitive';
    apply.textContent = 'Apply (restarts php, ~2s)';
    apply.onclick = () => applyPhpIni(name, apply);
    body.appendChild(form);
    body.appendChild(apply);
  } catch (e) {
    appendLine(body, '❌ ' + e.message, true);
  }
}

async function applyPhpIni(name, btn) {
  const val = id => document.getElementById(id).value.trim().toUpperCase();
  const num = id => parseInt(document.getElementById(id).value, 10);
  const payload = {
    memory_limit: val(`pi-memory-${name}`),
    upload_max_filesize: val(`pi-upload-${name}`),
    post_max_size: val(`pi-post-${name}`),
    max_execution_time: num(`pi-exec-${name}`),
    max_input_vars: num(`pi-vars-${name}`),
    max_input_time: num(`pi-time-${name}`),
    display_errors: document.getElementById(`pi-errors-${name}`).checked,
  };
  for (const field of ['memory_limit', 'upload_max_filesize', 'post_max_size']) {
    if (!PHP_SIZE_RE.test(payload[field])) { showToast(`Invalid ${field}: e.g. 128M`, true); return; }
  }
  btn.disabled = true;
  btn.textContent = 'Applying…';
  try {
    const res = await fetch(`${BASE}/php-ini/${encodeURIComponent(name)}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || res.statusText);
    showToast(`PHP settings applied to "${name}"`);
    closeBox(`out-${name}`);
  } catch (e) {
    showToast(e.message, true);
    btn.disabled = false;
    btn.textContent = 'Apply (restarts php, ~2s)';
  }
}

// ── Temporary sites: expiry badge + extend/make-permanent ────────────────────
function expiryBadge(p) {
  if (!p.expires_at) return '';
  const cls = p.days_left < 1 ? 'badge-red' : 'badge-yellow';
  const left = p.days_left < 1 ? `${Math.max(1, Math.round(p.days_left * 24))}h` : `${Math.round(p.days_left)}d`;
  return `<span class="badge ${cls}" title="Temporary site: destroyed automatically when it expires (no backups). Use ⏳ Lifetime to extend it.">⏳ expires in ${left}</span> `;
}

async function showExpiry(name, daysLeft) {
  const body = getOutputBox(`out-${name}`);
  appendLine(body, `⏳ "${name}" is a temporary site: it will be destroyed automatically in about ${daysLeft < 1 ? Math.max(1, Math.round(daysLeft * 24)) + ' hours' : Math.round(daysLeft) + ' days'} (no backups are kept).`);
  const wrap = document.createElement('div');
  wrap.className = 'php-inline-form';
  const extend = (days, label) => {
    const btn = document.createElement('button');
    btn.className = 'icon-btn';
    btn.textContent = label;
    btn.onclick = async () => {
      try {
        const res = await fetch(`${BASE}/expiry/${encodeURIComponent(name)}`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ lifetime_days: days }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || res.statusText);
        showToast(days === 0 ? `"${name}" is now permanent` : `"${name}" now expires in ${days} day${days === 1 ? '' : 's'}`);
        closeBox(`out-${name}`);
        loadProjects();
      } catch (e) { showToast(e.message, true); }
    };
    return btn;
  };
  wrap.append(extend(1, '+ 1 day from now'), document.createTextNode(' '),
              extend(7, '+ 7 days from now'), document.createTextNode(' '),
              extend(30, '+ 30 days from now'), document.createTextNode(' '),
              extend(0, 'Make permanent'));
  body.appendChild(wrap);
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

// ── System info: PHP image inventory ─────────────────────────────────────────
async function loadSystemInfo() {
  try {
    const [imagesData, disk, settings] = await Promise.all([
      fetch(`${BASE}/images`, { cache: 'no-store' }).then(r => r.json()),
      fetch(`${BASE}/disk`, { cache: 'no-store' }).then(r => r.json()),
      fetch(`${BASE}/images/settings`, { cache: 'no-store' }).then(r => r.json()),
    ]);
    renderImages(imagesData);
    renderDockerDisk(disk);
    document.getElementById('gc-days').value = settings.autodelete_days;
  } catch (e) {
    const alertBox = document.getElementById('images-alert');
    alertBox.hidden = false;
    alertBox.textContent = 'Unable to load system info: ' + e.message;
  }
}

function renderImages(data) {
  const body = document.getElementById('images-body');
  if (!data.images.length) {
    body.innerHTML = '<tr><td colspan="5">No PHP images yet — the first deploy will build one.</td></tr>';
    return;
  }
  body.innerHTML = data.images.map(img => {
    const age = img.stale
      ? `<span class="badge badge-yellow" title="Older than ${data.stale_days} days: the WordPress inside may be outdated. Refresh to update it.">${img.age_days}d — stale</span>`
      : `<span class="badge badge-green">${img.age_days}d</span>`;
    const used = img.used_by.length
      ? img.used_by.map(esc).join(', ')
      : '<span class="badge badge-gray">no sites</span>';
    const delBtn = img.used_by.length
      ? `<button class="icon-btn" disabled title="In use by ${esc(img.used_by.join(', '))} — cannot be deleted">Delete</button>`
      : `<button class="icon-btn" onclick="deleteImage('${esc(img.php_version)}')">Delete</button>`;
    return `<tr><td><b>PHP ${esc(img.php_version)}</b></td><td>${img.size_gb} GB</td><td>${age}</td><td>${used}</td>
      <td><button class="icon-btn sensitive" onclick="refreshImage('${esc(img.php_version)}')" title="Rebuild now with the latest WordPress (~5 min)">Refresh</button> ${delBtn}</td></tr>`;
  }).join('');
}

function renderDockerDisk(data) {
  const el = document.getElementById('docker-disk');
  const rows = data.docker.map(d =>
    `<tr><td>${esc(d.type)}</td><td>${d.size_gb} GB</td><td>${d.reclaimable_gb} GB reclaimable</td></tr>`).join('');
  el.innerHTML = `<table class="svc-table"><thead><tr><th>Type</th><th>Size</th><th>Reclaimable</th></tr></thead>
    <tbody>${rows}</tbody></table>
    <p class="field-help">Host filesystem: ${data.fs.used_gb} GB used of ${data.fs.total_gb} GB (${data.fs.free_gb} GB free).
    Reclaimable build cache is trimmed automatically after builds and weekly.</p>`;
}

function refreshImage(version) {
  if (blockedIfBusy()) return;
  if (!confirm(`Rebuild the PHP ${version} image now with the latest WordPress?\n\nThis takes about 5 minutes. Deploys are blocked while it runs; running sites are not touched (they pick the new image on their next recreate).`)) return;
  streamSSE(`${BASE}/images/refresh`, { php_version: version }, 'out-system', ok => {
    if (ok) showToast(`PHP ${version} image refreshed`);
    loadSystemInfo();
  });
}

async function deleteImage(version) {
  if (blockedIfBusy()) return;
  const warn = `Delete the PHP ${version} image?\n\n`
    + `It frees its disk space, but the NEXT deploy on PHP ${version} will rebuild it from scratch (about 5 minutes instead of ~35 seconds).\n\n`
    + `Type the version “${version}” to confirm.`;
  const typed = prompt(warn);
  if (typed === null) return;
  if (typed.trim() !== version) { showToast('Version mismatch: deletion cancelled', true); return; }
  try {
    const res = await sensitiveFetch(`${BASE}/images/delete`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ php_version: version, confirm: typed.trim() }),
    });
    const payload = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(payload.detail || res.statusText);
    showToast(`Image PHP ${version} deleted`);
  } catch (e) {
    showToast(e.message, true);
  }
  loadSystemInfo();
}

async function saveImageGc() {
  const days = parseInt(document.getElementById('gc-days').value, 10);
  if (isNaN(days) || days < 0 || days > 365) { showToast('Enter a number of days between 0 and 365', true); return; }
  try {
    const res = await fetch(`${BASE}/images/settings`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ autodelete_days: days }),
    });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
    showToast(days === 0 ? 'Auto-delete disabled (manual only)' : `Unused images will be deleted after ${days} days`);
  } catch (e) {
    showToast(e.message, true);
  }
}

// ── Boot ─────────────────────────────────────────────────────────────────────
updateClock();
loadPlatform();
if (document.body.dataset.page === 'deploy') loadBlueprints();
if (document.body.dataset.page === 'manage') loadProjects();
if (document.body.dataset.page === 'system') loadSystemInfo();
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

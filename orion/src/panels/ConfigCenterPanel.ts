import * as vscode from 'vscode';
import { orionHeaders } from '../services/authToken';

// ===========================================================================
// >>> P7.2: Orion Configuration Center (extension 0.5.x).
//
// One panel for every setting that does NOT live in .env (that stays the
// Setup Wizard's territory — single writer, single source of truth).
// Sesi P7.2: only the Project tab is functional. Model / MCP / Sub-agent
// / Data tabs ship in P7.3-P7.4.
//
// Architecture mirrors McpPanel exactly:
//   * extension host holds the token and does every HTTP call;
//   * webview is a thin UI over postMessage behind a strict CSP;
//   * one POST /config per Save is a single transaction:
//     validate -> atomic write -> live sync (no restart needed).
// ===========================================================================

const BACKEND = 'http://127.0.0.1:8000';

function activeProjectPath(): string {
  const folders = vscode.workspace.workspaceFolders;
  return folders && folders.length > 0 ? folders[0].uri.fsPath : '';
}

function withProjectQuery(url: string): string {
  const pp = activeProjectPath();
  return pp ? url + (url.includes('?') ? '&' : '?') + 'project_path=' + encodeURIComponent(pp) : url;
}

interface ConfigRange { [key: string]: [number, number]; }

interface ConfigSnapshot {
  project_path: string | null;
  config_path: string | null;
  orion_config: Record<string, unknown>;
  defaults: Record<string, unknown>;
  ranges: ConfigRange;
  env_summary: {
    gemini_keys_count: number;
    default_model: string;
    gemini_disabled: boolean;
    router_fallback: string;
    providers: Array<{ n: number; name: string; base_url: string; models: string[] }>;
    fallback: { has_key: boolean; models: string[] };
    cloudflare: { has_credentials: boolean; models: string[] };
  };

}

interface McpStatus {
  enabled: boolean;
  servers: Array<{ name: string; status: string; tools: number }>;
  warnings: string[];
}

async function fetchJson(url: string, init?: RequestInit): Promise<unknown> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 12000);
  try {
    const res = await fetch(url, { ...init, headers: orionHeaders(), signal: ctrl.signal });
    const body = (await res.json().catch(() => ({}))) as { error?: string };
    if (!res.ok) {
      throw new Error(body && body.error ? body.error : ('HTTP ' + res.status));
    }
    return body;
  } catch (e: unknown) {
    if (e instanceof Error && e.name === 'AbortError') {
      throw new Error('Backend did not answer in time — is it running?');
    }
    if (e instanceof TypeError) {
      throw new Error('Backend not reachable on port 8000.');
    }
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

function getNonce(): string {
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
  let text = '';
  for (let i = 0; i < 32; i++) { text += chars.charAt(Math.floor(Math.random() * chars.length)); }
  return text;
}

export class ConfigCenterPanel {
  public static currentPanel: ConfigCenterPanel | undefined;
  private readonly panel: vscode.WebviewPanel;
  private readonly disposables: vscode.Disposable[] = [];
  private lastNote = '';

  public static createOrShow(): void {
    const column = vscode.window.activeTextEditor
      ? vscode.window.activeTextEditor.viewColumn
      : undefined;
    if (ConfigCenterPanel.currentPanel) {
      ConfigCenterPanel.currentPanel.panel.reveal(column);
      return;
    }
    const panel = vscode.window.createWebviewPanel(
      'orionConfigCenter',
      'Orion Configuration Center',
      column || vscode.ViewColumn.One,
      { enableScripts: true, retainContextWhenHidden: true },
    );
    ConfigCenterPanel.currentPanel = new ConfigCenterPanel(panel);
  }

  private constructor(panel: vscode.WebviewPanel) {
    this.panel = panel;
    this.panel.webview.html = this.getHtml(this.panel.webview);
    this.panel.webview.onDidReceiveMessage(
      (msg: { cmd: string;[k: string]: unknown }) => { void this.onMessage(msg); },
      undefined,
      this.disposables,
    );
    this.panel.onDidDispose(() => this.dispose(), null, this.disposables);
  }

  public dispose(): void {
    ConfigCenterPanel.currentPanel = undefined;
    this.panel.dispose();
    for (const d of this.disposables) { d.dispose(); }
    this.disposables.length = 0;
  }

  private post(msg: Record<string, unknown>): void {
    void this.panel.webview.postMessage(msg);
  }

  private async pushState(): Promise<void> {
    let snapshot: ConfigSnapshot | null = null;
    let error: string | null = null;
    try {
      snapshot = (await fetchJson(withProjectQuery(BACKEND + '/config'))) as ConfigSnapshot;
    } catch (e: unknown) {
      error = e instanceof Error ? e.message : String(e);
    }
    this.post({ type: 'state', snapshot, error, note: this.lastNote });
    this.lastNote = '';
  }

  private async onMessage(msg: { cmd: string;[k: string]: unknown }): Promise<void> {
    switch (msg.cmd) {
      case 'ready':
      case 'refresh':
        await this.pushState();
        await this.pushMcpStatus(); // <--- TAMBAHKAN INI
        return;
      case 'refreshMcp':            // <--- TAMBAHKAN BLOK INI
        await this.pushMcpStatus();
        return;
      case 'save': {
        const incoming = msg.orion_config;
        if (!incoming || typeof incoming !== 'object') {
          this.lastNote = 'Save failed: payload missing.';
          await this.pushState();
          return;
        }
        try {
          const res = (await fetchJson(BACKEND + '/config', {
            method: 'POST',
            body: JSON.stringify({ orion_config: incoming, project_path: activeProjectPath() || undefined }),
          })) as { success: boolean; error?: string };
          if (!res || res.success !== true) {
            this.lastNote = 'Save failed: ' + (res?.error || 'unknown error');
          } else {
            this.lastNote = 'Saved. Takes effect on the next chat turn — no restart needed.';
          }
        } catch (e: unknown) {
          this.lastNote = 'Save failed: ' + (e instanceof Error ? e.message : String(e));
        }
        await this.pushState();
        return;
      }
      case 'reset': {
        // Fetch defaults from the last GET, then save them as a payload.
        try {
          const snap = (await fetchJson(withProjectQuery(BACKEND + '/config'))) as ConfigSnapshot;
          const defaults = snap.defaults || {};
          const res = (await fetchJson(BACKEND + '/config', {
            method: 'POST',
            body: JSON.stringify({ orion_config: defaults, project_path: activeProjectPath() || undefined }),
          })) as { success: boolean; error?: string };
          if (!res || res.success !== true) {
            this.lastNote = 'Reset failed: ' + (res?.error || 'unknown error');
          } else {
            this.lastNote = 'Reset to defaults applied.';
          }
        } catch (e: unknown) {
          this.lastNote = 'Reset failed: ' + (e instanceof Error ? e.message : String(e));
        }
        await this.pushState();
        return;
      }
      case 'openSetupWizard':
        await vscode.commands.executeCommand('orion.openSetupWizard');
        return;
      case 'openMcpPanel':
        await vscode.commands.executeCommand('orion.openMcpPanel');
        return;
      default:
        return;
    }
  }

  // ------------------------------------------------------------------- html

  private async pushMcpStatus(): Promise<void> {
    let status: McpStatus | null = null;
    let error: string | null = null;
    try {
      status = (await fetchJson(withProjectQuery(BACKEND + '/mcp/status'))) as McpStatus;
    } catch (e: unknown) {
      error = e instanceof Error ? e.message : String(e);
    }
    this.post({ type: 'mcpState', status, error });
  }


  private getHtml(webview: vscode.Webview): string {
    const nonce = getNonce();
    const csp = [
      "default-src 'none'",
      `img-src ${webview.cspSource} data:`,
      `style-src ${webview.cspSource} 'unsafe-inline'`,
      `script-src 'nonce-${nonce}'`,
      `font-src ${webview.cspSource}`,
    ].join('; ');
    // Inline page script avoids backticks so the TS template string
    // stays the only interpolation layer (wizard rule).
    return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="${csp}">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Orion Configuration Center</title>
<style>
  body { font-family: var(--vscode-font-family); color: var(--vscode-foreground); padding: 12px 16px; }
  h1 { font-size: 17px; margin: 0 0 4px; }
  .sub { opacity: .75; font-size: 12px; margin-bottom: 12px; }
  .tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--vscode-panel-border); margin-bottom: 12px; }
  .tab { padding: 6px 12px; cursor: pointer; border: 1px solid transparent; border-bottom: none; font-size: 12.5px; }
  .tab.active { border-color: var(--vscode-panel-border); border-bottom-color: var(--vscode-editor-background); margin-bottom: -1px; font-weight: 600; }
  .tab.dim { opacity: .55; }
  .pane { display: none; }
  .pane.active { display: block; }
  .bar { margin: 8px 0; display: flex; gap: 8px; flex-wrap: wrap; }
  button { cursor: pointer; }
  .note { padding: 6px 8px; margin: 8px 0; border: 1px solid var(--vscode-panel-border); }
  .warn { color: var(--vscode-errorForeground); }
  .ok { color: var(--vscode-testing-iconPassed); font-weight: 600; }
  .field { display: grid; grid-template-columns: 260px 1fr; gap: 8px; align-items: center; margin: 6px 0; font-size: 12.5px; }
  .field label { opacity: .9; }
  .field .hint { font-size: 11px; opacity: .6; grid-column: 1 / span 2; margin: -4px 0 4px 260px; }
  input[type=number], input[type=text] {
    width: 140px; box-sizing: border-box;
    background: var(--vscode-input-background); color: var(--vscode-input-foreground);
    border: 1px solid var(--vscode-input-border, #555); padding: 3px 6px; border-radius: 3px;
    font-size: 12px;
  }
  input[type=checkbox] { margin: 0; }
  .hidden { display: none; }
  .summary { font-size: 12px; opacity: .85; margin: 4px 0 8px 0; white-space: pre-wrap; }
  .pill { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 11.5px;
          background: var(--vscode-input-background); border: 1px solid var(--vscode-panel-border); margin-right: 4px; }
  .status-card {
    border: 1px solid var(--vscode-panel-border);
    border-radius: 4px;
    padding: 10px 12px;
    margin: 12px 0;
    background: var(--vscode-editor-background);
  }
  .status-row {
    display: flex;
    justify-content: space-between;
    padding: 4px 0;
    border-bottom: 1px dashed var(--vscode-panel-border, #33333355);
    font-size: 13px;
  }
  .status-row:last-child { border-bottom: none; }
  .status-label { opacity: 0.8; }
  .status-val { font-weight: 600; }
  .ok-val { color: var(--vscode-testing-iconPassed); }
  .warn-val { color: var(--vscode-errorForeground); }
</style>
</head>
<body>
<h1>Orion Configuration Center</h1>
<div class="sub" id="meta">Loading…</div>

<div class="tabs">
  <div class="tab active" data-tab="project">Project</div>
  <div class="tab dim" data-tab="model">Model</div>
  <div class="tab dim" data-tab="mcp">MCP</div>
  <div class="tab dim" data-tab="subagent">Sub-agent</div>
  <div class="tab dim" data-tab="data">Data</div>
</div>

<div id="note" class="note hidden"></div>

<!-- PROJECT TAB -->
<div class="pane active" id="pane-project">
  <p class="sub">Runtime settings for THIS project (stored in <code>.orion/orion_config.json</code>). Changes take effect on the next chat turn — no restart required.</p>

  <div class="field"><label for="fMaxIter">max_iterations</label><input id="fMaxIter" type="number" min="1" max="200" /></div>
  <div class="hint" id="hMaxIter">Limit for the agent thinking loop (1–200). Default: 20.</div>

  <div class="field"><label for="fTokenBudget">token_budget_threshold</label><input id="fTokenBudget" type="number" min="1000" max="200000" /></div>
  <div class="hint">Emergency context pruning threshold (1000–200000). Default: 12000.</div>

  <div class="field"><label for="fSubEnabled">subagents_enabled</label><input id="fSubEnabled" type="checkbox" /></div>
  <div class="hint">Master switch for internal sub-agents (Explorer / Verifier / Workers).</div>

  <div class="field"><label for="fSubPerTurn">subagent_max_per_turn</label><input id="fSubPerTurn" type="number" min="1" max="10" /></div>
  <div class="hint">Delegations per chat turn (1–10). Default: 3.</div>

  <div class="field"><label for="fSubMaxIter">subagent_max_iterations</label><input id="fSubMaxIter" type="number" min="1" max="30" /></div>
  <div class="hint">LLM rounds inside one sub-run (1–30). Default: 10.</div>

  <div class="field"><label for="fSubWorkers">subagent_workers_max</label><input id="fSubWorkers" type="number" min="1" max="6" /></div>
  <div class="hint">Parallel workers per delegation (1–6). Default: 3.</div>

  <div class="field"><label for="fInboxEnabled">inbox_enabled</label><input id="fInboxEnabled" type="checkbox" /></div>
  <div class="hint">Ambient inbox drop-zone (.orion/inbox/).</div>

  <div class="field"><label for="fInboxFiles">inbox_max_files</label><input id="fInboxFiles" type="number" min="1" max="20" /></div>
  <div class="hint">Files consumed per chat turn (1–20). Default: 5.</div>

  <div class="field"><label for="fMcpEnabled">mcp_enabled</label><input id="fMcpEnabled" type="checkbox" /></div>
  <div class="hint">Master switch for the MCP layer (per-server Disable lives in the MCP panel).</div>

  <div class="bar">
    <button id="btnSave">Save</button>
    <button id="btnReset" class="secondary" style="border:1px solid var(--vscode-panel-border);">Reset to defaults</button>
    <button id="btnRefresh">Refresh</button>
  </div>
</div>

<!-- MODEL TAB (P7.3) -->
<div class="pane" id="pane-model">
  <p class="sub">Read-only summary of your credentials from <code>.env</code>. To change keys or models, use the Setup Wizard (single writer).</p>
  
  <div class="status-card">
    <div class="status-row"><span class="status-label">Gemini Primary Keys</span> <span id="model-gemini-count" class="status-val">0</span></div>
    <div class="status-row"><span class="status-label">Default Model</span> <span id="model-default" class="status-val">-</span></div>
    <div class="status-row"><span class="status-label">Gemini Tier Status</span> <span id="model-gemini-status" class="status-val">-</span></div>
    <div class="status-row"><span class="status-label">Fallback Providers</span> <span id="model-providers-count" class="status-val">0</span></div>
    <div class="status-row"><span class="status-label">Last-Resort Fallback</span> <span id="model-fallback" class="status-val">none</span></div>
  </div>
  
  <div class="bar">
    <button id="btnOpenWizard1">Open Setup Wizard to edit keys</button>
  </div>
</div>

<!-- MCP TAB (P7.3) -->
<div class="pane" id="pane-mcp">
  <p class="sub">MCP servers are configured in the dedicated panel. This is a quick summary.</p>
  
  <div class="status-card">
    <div class="status-row"><span class="status-label">MCP Master Switch</span> <span id="mcp-enabled" class="status-val">-</span></div>
    <div class="status-row"><span class="status-label">Connected Servers</span> <span id="mcp-servers" class="status-val">-</span></div>
  </div>

  <div class="bar">
    <button id="btnOpenMcp">Open MCP Panel</button>
    <button id="btnRefreshMcp" class="secondary" style="border:1px solid var(--vscode-panel-border);">Refresh Status</button>
  </div>
</div>

<!-- SUB-AGENT TAB (P7.3) -->
<div class="pane" id="pane-subagent">
  <p class="sub">Per-role sub-agent model profiles. Empty means the sub-agent uses the main router.</p>

  <div class="status-card">
    <div class="status-row"><span class="status-label">Explorer Model</span> <span id="sub-explorer" class="status-val">Main Router (default)</span></div>
    <div class="status-row"><span class="status-label">Verifier Model</span> <span id="sub-verifier" class="status-val">Main Router (default)</span></div>
    <div class="status-row"><span class="status-label">Workers Model</span> <span id="sub-workers" class="status-val">Main Router (default)</span></div>
  </div>

  <div class="bar">
    <button id="btnOpenWizard2">Open Setup Wizard to edit profiles</button>
  </div>
</div>

<!-- DATA TAB (P7.4) -->
<div class="pane" id="pane-data">
  <p class="sub">File and folder locations that belong to Orion in this project. Nothing here is auto-deleted.</p>
  <div class="summary" id="dataList">Loading…</div>
</div>

<script nonce="${nonce}">
var vscode = acquireVsCodeApi();
var SNAPSHOT = null;

function el(id) { return document.getElementById(id); }
function post(m) { vscode.postMessage(m); }
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
  });
}

function val(key, fallback) {
  if (!SNAPSHOT || !SNAPSHOT.orion_config) { return fallback; }
  var v = SNAPSHOT.orion_config[key];
  return v === undefined || v === null ? fallback : v;
}

function renderProject() {
  if (!SNAPSHOT) { return; }
  var d = SNAPSHOT.defaults || {};
  el('fMaxIter').value = val('max_iterations', d.max_iterations || 20);
  el('fTokenBudget').value = val('token_budget_threshold', d.token_budget_threshold || 12000);
  el('fSubEnabled').checked = !!val('subagents_enabled', d.subagents_enabled !== false);
  el('fSubPerTurn').value = val('subagent_max_per_turn', d.subagent_max_per_turn || 3);
  el('fSubMaxIter').value = val('subagent_max_iterations', d.subagent_max_iterations || 10);
  el('fSubWorkers').value = val('subagent_workers_max', d.subagent_workers_max || 3);
  el('fInboxEnabled').checked = !!val('inbox_enabled', d.inbox_enabled !== false);
  el('fInboxFiles').value = val('inbox_max_files', d.inbox_max_files || 5);
  el('fMcpEnabled').checked = !!val('mcp_enabled', d.mcp_enabled !== false);
}

function renderModel() {
  if (!SNAPSHOT) { return; }
  var es = SNAPSHOT.env_summary || {};
  
  el('model-gemini-count').textContent = (es.gemini_keys_count || 0) + ' keys detected';
  el('model-default').textContent = es.default_model || '(unset)';
  
  var gStatus = el('model-gemini-status');
  if (es.gemini_disabled) {
    gStatus.textContent = 'DISABLED (explicit)';
    gStatus.className = 'status-val warn-val';
  } else if (es.gemini_keys_count > 0) {
    gStatus.textContent = 'ACTIVE';
    gStatus.className = 'status-val ok-val';
  } else {
    gStatus.textContent = 'No keys (skipped)';
    gStatus.className = 'status-val';
  }

  el('model-providers-count').textContent = (es.providers || []).length + ' group(s)';
  el('model-fallback').textContent = es.router_fallback || 'none';
}

function renderSubagent() {
  if (!SNAPSHOT) { return; }
  var profiles = SNAPSHOT.orion_config.subagent_models || {};
  
  function fmt(role) {
    var p = profiles[role];
    if (!p || !p.group || !p.model) { return 'Main Router (default)'; }
    return p.group + ' / ' + p.model;
  }
  
  el('sub-explorer').textContent = fmt('explorer');
  el('sub-verifier').textContent = fmt('verifier');
  el('sub-workers').textContent = fmt('workers');
}

function renderMcp(status, error) {
  var en = el('mcp-enabled');
  var sv = el('mcp-servers');
  if (error) {
    en.textContent = 'Error: ' + error;
    en.className = 'status-val warn-val';
    sv.textContent = '-';
    return;
  }
  if (!status) { return; }
  
  if (status.enabled) {
    en.textContent = 'ON';
    en.className = 'status-val ok-val';
  } else {
    en.textContent = 'OFF (disabled in config)';
    en.className = 'status-val warn-val';
  }
  
  var ok = status.servers.filter(function (s) { return s.status === 'ok'; }).length;
  var total = status.servers.length;
  sv.textContent = ok + ' of ' + total + ' connected';
  if (ok > 0) { sv.className = 'status-val ok-val'; }
  else { sv.className = 'status-val'; }
}

function renderData() {
  if (!SNAPSHOT) { return; }
  var pp = SNAPSHOT.project_path || '(none — run Set Project)';
  var cfg = SNAPSHOT.config_path || '(none)';
  var lines = [];
  lines.push('Project            : ' + pp);
  lines.push('Runtime config     : ' + cfg);
  lines.push('MCP config         : ' + (pp !== '(none — run Set Project)' ? pp + '\\\\.orion\\\\mcp.json' : '(n/a)'));
  lines.push('Semantic index     : ' + (pp !== '(none — run Set Project)' ? pp + '\\\\.orion\\\\semantic_index.db' : '(n/a)'));
  lines.push('Session history    : ' + (pp !== '(none — run Set Project)' ? pp + '\\\\.orion\\\\session_history.json' : '(n/a)'));
  lines.push('Logs               : ' + (pp !== '(none — run Set Project)' ? pp + '\\\\.orion\\\\logs\\\\' : '(n/a)'));
  el('dataList').textContent = lines.join('\\n');
}

function renderNote() {
  var n = el('note');
  if (SNAPSHOT && SNAPSHOT._note) {
    n.className = 'note';
    n.textContent = SNAPSHOT._note;
  } else {
    n.className = 'note hidden';
    n.textContent = '';
  }
}

function renderMeta() {
  if (!SNAPSHOT) { el('meta').textContent = 'Loading…'; return; }
  if (SNAPSHOT.error) {
    el('meta').innerHTML = '<span class="warn">Backend unreachable: ' + esc(SNAPSHOT.error) + '</span>';
    return;
  }
  var pp = (SNAPSHOT.snapshot && SNAPSHOT.snapshot.project_path) || '(none — run Set Project)';
  el('meta').textContent = 'Project: ' + pp;
}

function switchTab(name) {
  document.querySelectorAll('.tab').forEach(function (t) {
    t.classList.toggle('active', t.getAttribute('data-tab') === name);
  });
  document.querySelectorAll('.pane').forEach(function (p) {
    p.classList.toggle('active', p.id === 'pane-' + name);
  });
}

function collectProject() {
  var payload = {};
  payload.max_iterations = parseInt(el('fMaxIter').value, 10);
  payload.token_budget_threshold = parseInt(el('fTokenBudget').value, 10);
  payload.subagents_enabled = el('fSubEnabled').checked;
  payload.subagent_max_per_turn = parseInt(el('fSubPerTurn').value, 10);
  payload.subagent_max_iterations = parseInt(el('fSubMaxIter').value, 10);
  payload.subagent_workers_max = parseInt(el('fSubWorkers').value, 10);
  payload.inbox_enabled = el('fInboxEnabled').checked;
  payload.inbox_max_files = parseInt(el('fInboxFiles').value, 10);
  payload.mcp_enabled = el('fMcpEnabled').checked;
  Object.keys(payload).forEach(function (k) {
    if (typeof payload[k] === 'number' && isNaN(payload[k])) {
      payload[k] = null;
    }
  });
  return payload;
}

document.querySelectorAll('.tab').forEach(function (t) {
  t.addEventListener('click', function () {
    switchTab(t.getAttribute('data-tab'));
  });
});

el('btnRefresh').addEventListener('click', function () { post({ cmd: 'refresh' }); });
el('btnSave').addEventListener('click', function () {
  post({ cmd: 'save', orion_config: collectProject() });
});
el('btnReset').addEventListener('click', function () {
  if (confirm('Reset every Project setting to its default value? This overwrites .orion/orion_config.json (other keys are preserved).')) {
    post({ cmd: 'reset' });
  }
});
el('btnOpenWizard1').addEventListener('click', function () { post({ cmd: 'openSetupWizard' }); });
el('btnOpenWizard2').addEventListener('click', function () { post({ cmd: 'openSetupWizard' }); });
el('btnOpenMcp').addEventListener('click', function () { post({ cmd: 'openMcpPanel' }); });
el('btnRefreshMcp').addEventListener('click', function () { post({ cmd: 'refreshMcp' }); });

window.addEventListener('message', function (ev) {
  var m = ev.data || {};
  if (m.type === 'state') {
    SNAPSHOT = m.snapshot || { error: m.error };
    SNAPSHOT._note = m.note;
    if (SNAPSHOT.error) {
      renderMeta();
      return;
    }
    SNAPSHOT = { ...SNAPSHOT, snapshot: SNAPSHOT, error: null };
    SNAPSHOT.project_path = m.snapshot ? m.snapshot.project_path : null;
    SNAPSHOT.config_path = m.snapshot ? m.snapshot.config_path : null;
    SNAPSHOT.orion_config = (m.snapshot && m.snapshot.orion_config) || {};
    SNAPSHOT.defaults = (m.snapshot && m.snapshot.defaults) || {};
    SNAPSHOT.ranges = (m.snapshot && m.snapshot.ranges) || {};
    SNAPSHOT.env_summary = (m.snapshot && m.snapshot.env_summary) || {};
    renderMeta();
    renderProject();
    renderModel();
    renderSubagent();
    renderData();
    renderNote();
  } else if (m.type === 'mcpState') {
    renderMcp(m.status, m.error);
  }
});

post({ cmd: 'ready' });
</script>
</body>
</html>`;
  }
}
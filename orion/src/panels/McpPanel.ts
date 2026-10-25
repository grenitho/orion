import * as vscode from 'vscode';
import * as path from 'path';
import { orionHeaders } from '../services/authToken';

// ===========================================================================
// >>> UPDATE-015: the MCP Servers panel — the configuration surface for the
// MCP foundation that shipped in UPDATE-014 (backend/app/services/
// mcp_manager.py, docs/MCP_FOUNDATION.md).
//
// Same architecture as SetupWizardPanel: the extension host does ALL
// privileged work (HTTP calls with the shared token, opening files); the
// webview is a thin UI over postMessage behind a strict CSP (default-src
// 'none'), so nothing in the page can talk to the backend directly.
//
// Backend surface used (both token-guarded):
//   GET  /mcp/status   -> servers (env VALUES never included), warnings
//   POST /mcp/config   -> {op: set|remove|toggle|replace_all, ...}
//                         validate + atomic write + reload in one call
// ===========================================================================

const BACKEND = 'http://127.0.0.1:8000';

interface ServerRow {
    name: string;
    status: string;          // ok | error | disabled
    tools: number;
    error: string | null;
    disabled: boolean;
    command: string;
    args: string[];
    timeout: number;
    env_keys: string[];
}

interface McpStatus {
    project_path: string | null;
    config_path: string | null;
    warnings: string[];
    servers: ServerRow[];
    enabled?: boolean;  // UPDATE-017 master switch; UPDATE-020 makes it toggleable
}

interface ConfigResult { success: boolean; error: string | null; mcp?: unknown; }

async function fetchJson(url: string, init?: RequestInit): Promise<unknown> {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 10000);
    try {
        const res = await fetch(url, {
            ...init,
            headers: orionHeaders(),
            signal: ctrl.signal,
        });
        const body = (await res.json()) as { error?: string };
        if (!res.ok) {
            throw new Error(body && body.error ? body.error : ('HTTP ' + res.status));
        }
        return body;
    } finally {
        clearTimeout(timer);
    }
}

function postConfig(payload: Record<string, unknown>): Promise<ConfigResult> {
    return fetchJson(BACKEND + '/mcp/config', {
        method: 'POST',
        body: JSON.stringify(payload),
    }) as Promise<ConfigResult>;
}

function getNonce(): string {
    const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
    let text = '';
    for (let i = 0; i < 32; i++) {
        text += chars.charAt(Math.floor(Math.random() * chars.length));
    }
    return text;
}

export class McpPanel {
    public static currentPanel: McpPanel | undefined;

    public static createOrShow(): void {
        const column = vscode.window.activeTextEditor
            ? vscode.window.activeTextEditor.viewColumn
            : undefined;
        if (McpPanel.currentPanel) {
            McpPanel.currentPanel.panel.reveal(column);
            return;
        }
        const panel = vscode.window.createWebviewPanel(
            'orionMcpPanel',
            'Orion MCP Servers',
            column || vscode.ViewColumn.One,
            { enableScripts: true, retainContextWhenHidden: true }
        );
        McpPanel.currentPanel = new McpPanel(panel);
    }

    /** One-click demo install, shared with the Setup Wizard card (015). */
    public static async installDemoServer(): Promise<string> {
        try {
            const health = (await fetchJson(BACKEND + '/health')) as {
                python_executable?: string;
                backend_path?: string;
            };
            if (!health.python_executable || !health.backend_path) {
                return 'Demo install failed: /health did not report python_executable/backend_path — is the backend older than 0.6.0?';
            }
            const demoPath = path.join(health.backend_path, 'examples', 'mcp_demo_server.py');
            const res = await postConfig({
                op: 'set',
                name: 'demo',
                spec: { command: health.python_executable, args: [demoPath] },
            });
            return res.success
                ? "Demo server 'demo' installed and started (3 tools: echo, add, fail)."
                : ('Demo install failed: ' + (res.error || 'unknown error'));
        } catch (e: unknown) {
            return 'Demo install failed: ' + String(e instanceof Error ? e.message : e);
        }
    }

    private readonly panel: vscode.WebviewPanel;
    private readonly disposables: vscode.Disposable[] = [];
    private lastNote = '';

    private constructor(panel: vscode.WebviewPanel) {
        this.panel = panel;
        this.panel.webview.html = this.getHtml(this.panel.webview);
        this.panel.webview.onDidReceiveMessage(
            (msg: { cmd: string; [k: string]: unknown }) => { void this.onMessage(msg); },
            undefined,
            this.disposables
        );
        this.panel.onDidDispose(() => this.dispose(), null, this.disposables);
    }

    public dispose(): void {
        McpPanel.currentPanel = undefined;
        this.panel.dispose();
        for (const d of this.disposables) { d.dispose(); }
        this.disposables.length = 0;
    }

    private post(msg: Record<string, unknown>): void {
        void this.panel.webview.postMessage(msg);
    }

    private async pushState(): Promise<void> {
        let status: McpStatus | null = null;
        let error: string | null = null;
        try {
            status = (await fetchJson(BACKEND + '/mcp/status')) as McpStatus;
        } catch (e: unknown) {
            error = String(e instanceof Error ? e.message : e);
        }
        this.post({ type: 'state', status, error, note: this.lastNote });
        this.lastNote = '';
    }

    private async onMessage(msg: { cmd: string; [k: string]: unknown }): Promise<void> {
        switch (msg.cmd) {
            case 'ready':
            case 'refresh':
                await this.pushState();
                return;
            case 'save': {
                const name = String(msg.name || '').trim();
                const argsText = String(msg.argsText || '');
                const envText = String(msg.envText || '');
                const spec: Record<string, unknown> = {
                    command: String(msg.command || '').trim(),
                    args: argsText.split('\n').map((s) => s.trim()).filter((s) => s.length > 0),
                    disabled: Boolean(msg.disabled),
                };
                const timeout = Number(msg.timeout);
                if (msg.timeout !== '' && msg.timeout !== null && Number.isFinite(timeout) && timeout > 0) {
                    spec.timeoutSeconds = timeout;
                }
                const keepEnv = envText.trim() === '';
                if (!keepEnv) {
                    const env: Record<string, string> = {};
                    for (const line of envText.split('\n')) {
                        const trimmed = line.trim();
                        if (!trimmed || trimmed.startsWith('#')) { continue; }
                        const idx = trimmed.indexOf('=');
                        if (idx > 0) { env[trimmed.slice(0, idx).trim()] = trimmed.slice(idx + 1).trim(); }
                    }
                    spec.env = env;
                }
                try {
                    const res = await postConfig({ op: 'set', name, spec, keep_env: keepEnv });
                    this.lastNote = res.success
                        ? ("Server '" + name + "' saved and reloaded.")
                        : ('Save failed: ' + (res.error || 'unknown error'));
                } catch (e: unknown) {
                    this.lastNote = 'Save failed: ' + String(e instanceof Error ? e.message : e);
                }
                await this.pushState();
                return;
            }
            case 'remove': {
                const name = String(msg.name || '');
                try {
                    const res = await postConfig({ op: 'remove', name });
                    this.lastNote = res.success
                        ? ("Server '" + name + "' removed.")
                        : ('Remove failed: ' + (res.error || 'unknown error'));
                } catch (e: unknown) {
                    this.lastNote = 'Remove failed: ' + String(e instanceof Error ? e.message : e);
                }
                await this.pushState();
                return;
            }
            // >>> UPDATE-020: the master-switch button ("stop kontak") —
            // one /mcp/config op that writes mcp_enabled into orion_config
            // and applies it live (off stops every server, on re-starts).
            case 'masterSwitch': {
                const enabled = msg.enabled === true;
                try {
                    const res = await postConfig({ op: 'master_switch', enabled });
                    this.lastNote = res.success
                        ? (enabled
                            ? 'MCP master switch ON — servers started again.'
                            : 'MCP master switch OFF — all servers stopped; mcp.json preserved.')
                        : ('Master switch failed: ' + (res.error || 'unknown error'));
                } catch (e: unknown) {
                    this.lastNote = 'Master switch failed: ' + (e instanceof Error ? e.message : String(e));
                }
                await this.pushState();
                return;
            }
            case 'toggle': {
                const name = String(msg.name || '');
                try {
                    const res = await postConfig({
                        op: 'toggle', name, disabled: Boolean(msg.disabled),
                    });
                    this.lastNote = res.success
                        ? ("Server '" + name + "' " + (msg.disabled ? 'disabled.' : 'enabled.'))
                        : ('Toggle failed: ' + (res.error || 'unknown error'));
                } catch (e: unknown) {
                    this.lastNote = 'Toggle failed: ' + String(e instanceof Error ? e.message : e);
                }
                await this.pushState();
                return;
            }
            case 'demo':
                this.lastNote = await McpPanel.installDemoServer();
                await this.pushState();
                return;
            case 'openJson': {
                const cfg = String(msg.path || '');
                if (!cfg) {
                    this.lastNote = 'No mcp.json path known yet (set a project first).';
                    await this.pushState();
                    return;
                }
                try {
                    const doc = await vscode.workspace.openTextDocument(vscode.Uri.file(cfg));
                    await vscode.window.showTextDocument(doc, { preview: false });
                } catch (e: unknown) {
                    this.lastNote = 'Cannot open mcp.json: ' + String(e instanceof Error ? e.message : e);
                    await this.pushState();
                }
                return;
            }
            default:
                return;
        }
    }

    // ------------------------------------------------------------------- html

    private getHtml(webview: vscode.Webview): string {
        const nonce = getNonce();
        const csp = [
            "default-src 'none'",
            `img-src ${webview.cspSource} data:`,
            `style-src ${webview.cspSource} 'unsafe-inline'`,
            `script-src 'nonce-${nonce}'`,
            `font-src ${webview.cspSource}`,
        ].join('; ');
        // The inline page script avoids backticks on purpose: this TS
        // template string stays the only interpolation layer (wizard rule).
        return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="${csp}">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Orion MCP Servers</title>
<style>
  body { font-family: var(--vscode-font-family); color: var(--vscode-foreground); padding: 12px 16px; }
  h1 { font-size: 17px; margin: 0 0 4px; }
  .sub { opacity: .75; font-size: 12px; margin-bottom: 12px; }
  .bar { margin: 8px 0; display: flex; gap: 8px; flex-wrap: wrap; }
  button { cursor: pointer; }
  table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
  th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--vscode-panel-border); vertical-align: top; }
  .ok { color: var(--vscode-testing-iconPassed); font-weight: 600; }
  .err { color: var(--vscode-errorForeground); font-weight: 600; }
  .off { opacity: .6; }
  .note { padding: 6px 8px; margin: 8px 0; border: 1px solid var(--vscode-panel-border); }
  .warn { color: var(--vscode-errorForeground); }
  form { border: 1px solid var(--vscode-panel-border); padding: 10px; margin: 10px 0; }
  label { display: block; margin: 6px 0 2px; font-size: 12px; }
  input, textarea { width: 100%; box-sizing: border-box; background: var(--vscode-input-background); color: var(--vscode-input-foreground); border: 1px solid var(--vscode-input-border, transparent); padding: 4px 6px; font-family: var(--vscode-editor-font-family, monospace); font-size: 12px; }
  textarea { min-height: 54px; }
  .hidden { display: none; }
  .errline { font-size: 11.5px; color: var(--vscode-errorForeground); }
</style>
</head>
<body>
<h1>MCP tool servers</h1>
<div class="sub" id="meta">Loading…</div>
<div class="bar">
  <button id="btnRefresh">Refresh</button>
  <button id="btnMaster" title="Master switch: stops/starts the whole MCP layer for this project (mcp_enabled in orion_config.json); mcp.json is preserved"></button>
  <button id="btnAdd">Add server</button>
  <button id="btnDemo">Install demo server</button>
  <button id="btnJson">Open mcp.json</button>
</div>
<div id="note" class="note hidden"></div>
<div id="warns"></div>
<table id="tbl"><thead><tr>
  <th>Server</th><th>Status</th><th>Tools</th><th>Command</th><th></th>
</tr></thead><tbody id="rows"></tbody></table>

<form id="form" class="hidden">
  <h3 id="formTitle" style="margin:0 0 6px;font-size:14px;">Add server</h3>
  <label>Name (unique, e.g. fetch)</label><input id="fName" />
  <label>Command (absolute path or executable)</label><input id="fCmd" />
  <label>Args (one per line)</label><textarea id="fArgs"></textarea>
  <label>Timeout seconds (optional, 1–600, default 30)</label><input id="fTimeout" type="number" min="1" max="600" />
  <label>Env KEY=VALUE (one per line). Stored values are NEVER shown here — leave empty to keep the existing keys of this server, or re-type ALL pairs to replace them.</label><textarea id="fEnv"></textarea>
  <label><input id="fDisabled" type="checkbox" style="width:auto;" /> disabled (configured but not started)</label>
  <div class="bar">
    <button type="button" id="btnSave">Save &amp; reload</button>
    <button type="button" id="btnCancel">Cancel</button>
  </div>
</form>

<script nonce="${nonce}">
  var vscode = acquireVsCodeApi();
  var STATE = null;
  function el(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function post(m) { vscode.postMessage(m); }

  function badge(s) {
    if (s.status === 'ok') { return '<span class="ok">ok</span>'; }
    if (s.status === 'disabled') { return '<span class="off">disabled</span>'; }
    return '<span class="err">error</span>';
  }

  function render() {
    var meta = el('meta');
    var rows = el('rows');
    var warns = el('warns');
    if (!STATE) { meta.textContent = 'Loading…'; return; }
    if (STATE.error) {
      meta.innerHTML = '<span class="err">Backend unreachable: ' + esc(STATE.error) + '</span>';
      rows.innerHTML = '';
      return;
    }
    var st = STATE.status;
    meta.textContent = 'Project: ' + (st.project_path || '(none — run Set Project)') +
      '   ·   config: ' + (st.config_path || '(none)');
    // >>> UPDATE-020: master-switch button label mirrors the live state.
    var bm = el('btnMaster');
    if (st.enabled === false) {
      bm.textContent = 'MCP OFF — turn ON (master switch)';
    } else {
      bm.textContent = 'MCP ON — turn OFF (master switch)';
    }
    var note = el('note');
    if (STATE.note) { note.className = 'note'; note.textContent = STATE.note; }
    else { note.className = 'note hidden'; }
    warns.innerHTML = (st.warnings || []).map(function (w) {
      return '<div class="note warn">' + esc(w) + '</div>';
    }).join('');
    rows.innerHTML = (st.servers || []).map(function (s) {
      var acts = '<button data-act="toggle" data-name="' + esc(s.name) + '" data-dis="' + (s.disabled ? '0' : '1') + '">' +
        (s.disabled ? 'Enable' : 'Disable') + '</button> ' +
        '<button data-act="edit" data-name="' + esc(s.name) + '">Edit</button> ' +
        '<button data-act="remove" data-name="' + esc(s.name) + '">Remove</button>';
      var errline = s.error ? '<div class="errline">' + esc(s.error) + '</div>' : '';
      var envline = (s.env_keys && s.env_keys.length)
        ? '<div class="errline">env keys: ' + esc(s.env_keys.join(', ')) + ' (values hidden)</div>' : '';
      return '<tr><td>' + esc(s.name) + envline + '</td><td>' + badge(s) + errline + '</td><td>' +
        s.tools + '</td><td>' + esc(s.command) + ' ' + esc((s.args || []).join(' ')) +
        '</td><td>' + acts + '</td></tr>';
    }).join('') || '<tr><td colspan="5" class="off">No servers configured yet.</td></tr>';
  }

  function openForm(row) {
    var f = el('form');
    f.className = '';
    if (row) {
      el('formTitle').textContent = 'Edit server: ' + row.name;
      el('fName').value = row.name;
      el('fName').disabled = true;
      el('fCmd').value = row.command || '';
      el('fArgs').value = (row.args || []).join('\\n');
      el('fTimeout').value = row.timeout || '';
      el('fEnv').value = '';
      el('fDisabled').checked = !!row.disabled;
    } else {
      el('formTitle').textContent = 'Add server';
      el('fName').value = '';
      el('fName').disabled = false;
      el('fCmd').value = '';
      el('fArgs').value = '';
      el('fTimeout').value = '';
      el('fEnv').value = '';
      el('fDisabled').checked = false;
    }
  }

  el('btnRefresh').onclick = function () { post({ cmd: 'refresh' }); };
  el('btnMaster').onclick = function () {
    var st = STATE && STATE.status;
    var currentlyOn = !st || st.enabled !== false;
    post({ cmd: 'masterSwitch', enabled: !currentlyOn });
  };
  el('btnAdd').onclick = function () { openForm(null); };
  el('btnDemo').onclick = function () { post({ cmd: 'demo' }); };
  el('btnJson').onclick = function () {
    post({ cmd: 'openJson', path: STATE && STATE.status ? STATE.status.config_path : '' });
  };
  el('btnCancel').onclick = function () { el('form').className = 'hidden'; };
  el('btnSave').onclick = function () {
    post({
      cmd: 'save',
      name: el('fName').value,
      command: el('fCmd').value,
      argsText: el('fArgs').value,
      timeout: el('fTimeout').value,
      envText: el('fEnv').value,
      disabled: el('fDisabled').checked
    });
    el('form').className = 'hidden';
  };
  el('rows').onclick = function (ev) {
    var b = ev.target;
    if (!b || !b.getAttribute) { return; }
    var act = b.getAttribute('data-act');
    var name = b.getAttribute('data-name');
    if (!act) { return; }
    if (act === 'toggle') {
      post({ cmd: 'toggle', name: name, disabled: b.getAttribute('data-dis') === '1' });
    } else if (act === 'remove') {
      post({ cmd: 'remove', name: name });
    } else if (act === 'edit' && STATE && STATE.status) {
      var row = null;
      (STATE.status.servers || []).forEach(function (s) { if (s.name === name) { row = s; } });
      openForm(row);
    }
  };

  window.addEventListener('message', function (ev) {
    var m = ev.data;
    if (m && m.type === 'state') { STATE = m; render(); }
  });
  post({ cmd: 'ready' });
</script>
</body>
</html>`;
    }
}

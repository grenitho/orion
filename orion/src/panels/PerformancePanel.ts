import * as fs from 'fs';
import * as path from 'path';
import * as vscode from 'vscode';
import { orionHeaders } from '../services/authToken';

// >>> UPDATE-025 commit 4: the dashboard stopped being a "last 10"
// peephole. The host reads the WHOLE performance_log.jsonl from the
// active project's .orion folder (path learned from /health, which the
// backend already exposes) and ships it to the page; the page owns
// role/model filtering and per-row token columns. Field names follow
// perf_log.py exactly (input_tokens/output_tokens/estimated_cost_usd) —
// the pre-0.4.4 page asked for input_chars/output_chars, which never
// existed, hence the "undefined / undefined" column owners saw.
const MAX_ENTRIES_SHIPPED = 5000;
const MAX_ROWS_RENDERED = 500;

export class PerformancePanel {
    public static currentPanel: PerformancePanel | undefined;
    private readonly _panel: vscode.WebviewPanel;

    private constructor(panel: vscode.WebviewPanel) {
        this._panel = panel;
        this._panel.onDidDispose(() => this.dispose(), null);
        // >>> UPDATE-025 commit 3: the page asks, the HOST fetches (a
        // webview page cannot hold the X-Orion-Token secret). Commit 4
        // adds the on-disk full-log read to the same bridge.
        this._panel.webview.onDidReceiveMessage(
            (msg: { command?: string }) => {
                if (msg && msg.command === 'loadPerf') {
                    void this._pushPerf();
                }
            },
            null,
        );
        this._update();
    }

    public static createOrShow() {
        const column = vscode.window.activeTextEditor ? vscode.window.activeTextEditor.viewColumn : undefined;
        const panel = vscode.window.createWebviewPanel(
            'orionPerformance',
            'Orion Performance Dashboard',
            column || vscode.ViewColumn.Beside,
            {
                enableScripts: true,
                retainContextWhenHidden: true
            }
        );
        PerformancePanel.currentPanel = new PerformancePanel(panel);
    }

    private _readFullLog(projectPath: string): unknown[] {
        try {
            const file = path.join(projectPath, '.orion', 'performance_log.jsonl');
            const raw = fs.readFileSync(file, 'utf-8');
            const lines = raw.split('\n').filter(l => l.trim().length > 0);
            const tail = lines.slice(-MAX_ENTRIES_SHIPPED);
            const entries: unknown[] = [];
            for (const line of tail) {
                try {
                    entries.push(JSON.parse(line));
                } catch {
                    // a torn last line (crash mid-append) is skipped, not fatal
                }
            }
            return entries;
        } catch {
            return [];
        }
    }

    private async _pushPerf(): Promise<void> {
        try {
            const headers = orionHeaders();
            const res = await fetch('http://127.0.0.1:8000/performance', { headers });
            const data = (await res.json()) as { summary?: unknown; recent_logs?: unknown[] };
            let logs: unknown[] = [];
            let logSource = 'none';
            try {
                const healthRes = await fetch('http://127.0.0.1:8000/health', { headers });
                const health = (await healthRes.json()) as { project_path?: unknown };
                if (health && typeof health.project_path === 'string' && health.project_path) {
                    logs = this._readFullLog(health.project_path);
                    logSource = logs.length > 0 ? 'file' : 'empty';
                }
            } catch {
                // health unreachable: fall through to recent_logs below
            }
            if (logs.length === 0 && Array.isArray(data.recent_logs)) {
                logs = data.recent_logs;
                logSource = 'recent';
            }
            void this._panel.webview.postMessage({
                type: 'perfData',
                data: { summary: data.summary ?? null, logs, logSource },
            });
        } catch (e: unknown) {
            const message = e instanceof Error ? e.message : String(e);
            void this._panel.webview.postMessage({ type: 'perfError', message });
        }
    }

    private _update() {
        this._panel.webview.html = this._getHtmlForWebview();
    }

    private _getHtmlForWebview() {
        return `
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="UTF-8">
                <style>
                    body { 
                        color: var(--vscode-foreground); 
                        background-color: var(--vscode-editor-background); 
                        font-family: var(--vscode-font-family); 
                        padding: 20px; 
                    }
                    h1 { 
                        font-size: 1.5em; 
                        margin-bottom: 20px; 
                        border-bottom: 1px solid var(--vscode-panel-border); 
                        padding-bottom: 10px; 
                    }
                    .cards { 
                        display: flex; 
                        gap: 15px; 
                        flex-wrap: wrap; 
                        margin-bottom: 20px; 
                    }
                    .card { 
                        background-color: var(--vscode-badge-background); 
                        color: var(--vscode-badge-foreground); 
                        padding: 15px; 
                        border-radius: 6px; 
                        flex: 1; 
                        min-width: 150px; 
                        box-shadow: 0 2px 4px rgba(0,0,0,0.1); 
                    }
                    .card h3 { 
                        margin: 0 0 10px 0; 
                        font-size: 0.9em; 
                        opacity: 0.8; 
                    }
                    .card .value { 
                        font-size: 1.8em; 
                        font-weight: bold; 
                    }
                    .filters {
                        display: flex;
                        gap: 12px;
                        align-items: center;
                        flex-wrap: wrap;
                        margin: 0 0 10px 0;
                    }
                    .filters label { font-size: 0.9em; opacity: 0.85; }
                    .filters select {
                        background: var(--vscode-dropdown-background);
                        color: var(--vscode-dropdown-foreground);
                        border: 1px solid var(--vscode-dropdown-border);
                        padding: 4px 8px;
                        border-radius: 4px;
                    }
                    .filtsum {
                        font-size: 0.9em;
                        opacity: 0.85;
                        margin: 0 0 12px 0;
                    }
                    table { 
                        width: 100%; 
                        border-collapse: collapse; 
                        font-size: 0.9em; 
                    }
                    th, td { 
                        padding: 8px 12px; 
                        text-align: left; 
                        border-bottom: 1px solid var(--vscode-panel-border); 
                    }
                    th { 
                        background-color: var(--vscode-list-hoverBackground); 
                    }
                    .status-success { 
                        color: #4caf50; 
                        font-weight: bold; 
                    }
                    .status-error { 
                        color: #f44336; 
                        font-weight: bold; 
                    }
                    .role-badge {
                        padding: 2px 8px;
                        border-radius: 10px;
                        font-size: 0.85em;
                        font-weight: bold;
                    }
                    .role-main { color: #9e9e9e; }
                    .role-explorer { color: #b388ff; }
                    .role-verifier { color: #4caf50; }
                    .role-worker { color: #2196f3; }
                    .loading { 
                        text-align: center; 
                        padding: 20px; 
                        opacity: 0.7; 
                    }
                </style>
            </head>
            <body>
                <h1>📊 Orion Performance Dashboard</h1>
                <div id="dashboard-content">
                    <div class="loading">Loading statistics...</div>
                </div>

                <script>
                    const vscode = acquireVsCodeApi();
                    let ALL_LOGS = [];
                    let SUMMARY = null;

                    function esc(v) {
                        return String(v === undefined || v === null ? '' : v)
                            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
                    }

                    function filtered() {
                        const role = document.getElementById('fltRole').value;
                        const model = document.getElementById('fltModel').value;
                        return ALL_LOGS.filter(l => {
                            const r = l.role || 'main';
                            if (role !== 'all' && r !== role) { return false; }
                            if (model !== 'all' && l.model !== model) { return false; }
                            return true;
                        });
                    }

                    function renderTable() {
                        const rows = filtered().slice().reverse();
                        const total = rows.length;
                        const shown = rows.slice(0, ${MAX_ROWS_RENDERED});
                        let inT = 0, outT = 0, cost = 0;
                        rows.forEach(l => {
                            inT += Number(l.input_tokens) || 0;
                            outT += Number(l.output_tokens) || 0;
                            cost += Number(l.estimated_cost_usd) || 0;
                        });
                        let html = '<div class="filtsum">Showing ' + total + ' of ' + ALL_LOGS.length +
                            ' entries — filtered: ' + inT.toLocaleString() + ' in tok · ' +
                            outT.toLocaleString() + ' out tok · $' + cost.toFixed(6) + '</div>';
                        if (total === 0) {
                            html += '<div class="loading">No entries match the current filter.</div>';
                            document.getElementById('table-zone').innerHTML = html;
                            return;
                        }
                        html += '<table><thead><tr><th>Time</th><th>Role</th><th>Model</th>' +
                            '<th>In tok</th><th>Out tok</th><th>Cost (USD)</th><th>ms</th><th>Status</th>' +
                            '</tr></thead><tbody>';
                        shown.forEach(log => {
                            const time = new Date(log.timestamp).toLocaleTimeString();
                            const statusClass = log.status === 'success' ? 'status-success' : 'status-error';
                            const role = log.role || 'main';
                            html += '<tr>';
                            html += '<td>' + esc(time) + '</td>';
                            html += '<td><span class="role-badge role-' + esc(role) + '">' + esc(role) + '</span></td>';
                            html += '<td>' + esc(log.model) + '</td>';
                            html += '<td>' + (Number(log.input_tokens) || 0).toLocaleString() + '</td>';
                            html += '<td>' + (Number(log.output_tokens) || 0).toLocaleString() + '</td>';
                            html += '<td>$' + (Number(log.estimated_cost_usd) || 0).toFixed(6) + '</td>';
                            html += '<td>' + esc(log.duration_ms) + '</td>';
                            html += '<td class="' + statusClass + '">' + esc(log.status) + '</td>';
                            html += '</tr>';
                        });
                        html += '</tbody></table>';
                        if (total > shown.length) {
                            html += '<div class="loading">Showing first ' + shown.length +
                                ' of ' + total + ' matches — narrow the filter to see the rest.</div>';
                        }
                        document.getElementById('table-zone').innerHTML = html;
                    }

                    function renderPerf(payload) {
                        const data = payload || {};
                        SUMMARY = data.summary;
                        ALL_LOGS = Array.isArray(data.logs) ? data.logs : [];
                        let html = '';
                        if (SUMMARY) {
                            const s = SUMMARY;
                            html += '<div class="cards">';
                            html += '<div class="card"><h3>Total Requests (all-time)</h3><div class="value">' + esc(s.total_requests) + '</div></div>';
                            html += '<div class="card"><h3>Total Input Tokens</h3><div class="value" style="color: #4caf50;">' + Number(s.total_input_tokens || 0).toLocaleString() + '</div></div>';
                            html += '<div class="card"><h3>Total Output Tokens</h3><div class="value" style="color: #2196f3;">' + Number(s.total_output_tokens || 0).toLocaleString() + '</div></div>';
                            html += '<div class="card"><h3>Estimated Cost (USD)</h3><div class="value">$' + Number(s.total_cost_usd || 0).toFixed(6) + '</div></div>';
                            html += '<div class="card"><h3>Success / Failed</h3><div class="value"><span class="status-success">' + esc(s.success_count) + '</span> / <span class="status-error">' + esc(s.error_count) + '</span></div></div>';
                            html += '</div>';
                        }
                        const roles = ['all'];
                        const models = ['all'];
                        ALL_LOGS.forEach(l => {
                            const r = l.role || 'main';
                            if (roles.indexOf(r) < 0) { roles.push(r); }
                            if (l.model && models.indexOf(l.model) < 0) { models.push(l.model); }
                        });
                        html += '<div class="filters">';
                        html += '<label>Role <select id="fltRole">' + roles.map(r => '<option value="' + esc(r) + '">' + esc(r) + '</option>').join('') + '</select></label>';
                        html += '<label>Model <select id="fltModel">' + models.map(m => '<option value="' + esc(m) + '">' + esc(m) + '</option>').join('') + '</select></label>';
                        html += '</div>';
                        html += '<div id="table-zone"></div>';
                        document.getElementById('dashboard-content').innerHTML = html;
                        document.getElementById('fltRole').addEventListener('change', renderTable);
                        document.getElementById('fltModel').addEventListener('change', renderTable);
                        renderTable();
                    }

                    window.addEventListener('message', event => {
                        const msg = event.data || {};
                        if (msg.type === 'perfData') {
                            renderPerf(msg.data);
                        } else if (msg.type === 'perfError') {
                            document.getElementById('dashboard-content').innerHTML =
                                '<div class="loading">Failed to query the backend (' + esc(msg.message) + '). Make sure the Orion backend is running.</div>';
                        }
                    });

                    vscode.postMessage({ command: 'loadPerf' });
                </script>
            </body>
            </html>
        `;
    }

    public dispose() {
        PerformancePanel.currentPanel = undefined;
        this._panel.dispose();
    }
}

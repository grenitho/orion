import * as vscode from 'vscode';

export class PerformancePanel {
    public static currentPanel: PerformancePanel | undefined;
    private readonly _panel: vscode.WebviewPanel;

    private constructor(panel: vscode.WebviewPanel) {
        this._panel = panel;
        this._panel.onDidDispose(() => this.dispose(), null);
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
                        margin-bottom: 30px; 
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
                    
                    async function loadData() {
                        try {
                            const res = await fetch('http://127.0.0.1:8000/performance');
                            const data = await res.json();
                            
                            if (data.error) {
                                document.getElementById('dashboard-content').innerHTML = '<div class="loading">Error: ' + data.error + '</div>';
                                return;
                            }

                            const s = data.summary;
                            const logs = data.recent_logs || [];

                            let html = '<div class="cards">';
                            html += '<div class="card"><h3>Total Requests</h3><div class="value">' + s.total_requests + '</div></div>';
                            html += '<div class="card"><h3>Total Input Tokens</h3><div class="value" style="color: #4caf50;">' + s.total_input_tokens.toLocaleString() + '</div></div>';
                            html += '<div class="card"><h3>Total Output Tokens</h3><div class="value" style="color: #2196f3;">' + s.total_output_tokens.toLocaleString() + '</div></div>';
                            html += '<div class="card"><h3>Estimated Cost (USD)</h3><div class="value">$' + s.total_cost_usd.toFixed(6) + '</div></div>';
                            html += '<div class="card"><h3>Average Time (ms)</h3><div class="value">' + s.avg_response_time_ms + ' ms</div></div>';
                            html += '<div class="card"><h3>Success / Failed</h3><div class="value"><span class="status-success">' + s.success_count + '</span> / <span class="status-error">' + s.error_count + '</span></div></div>';
                            html += '</div>';

                            if (logs.length > 0) {
                                html += '<h3>Last 10 Requests</h3>';
                                html += '<table><thead><tr><th>Time</th><th>Model</th><th>Input/Output (chars)</th><th>Time (ms)</th><th>Status</th></tr></thead><tbody>';
                                logs.reverse().forEach(log => {
                                    const time = new Date(log.timestamp).toLocaleTimeString();
                                    const statusClass = log.status === 'success' ? 'status-success' : 'status-error';
                                    html += '<tr>';
                                    html += '<td>' + time + '</td>';
                                    html += '<td>' + log.model + '</td>';
                                    html += '<td>' + log.input_chars + ' / ' + log.output_chars + '</td>';
                                    html += '<td>' + log.duration_ms + '</td>';
                                    html += '<td class="' + statusClass + '">' + log.status + '</td>';
                                    html += '</tr>';
                                });
                                html += '</tbody></table>';
                            } else {
                                html += '<div class="loading">No log data yet. Try chatting with Orion first.</div>';
                            }

                            document.getElementById('dashboard-content').innerHTML = html;
                        } catch (e) {
                            document.getElementById('dashboard-content').innerHTML = '<div class="loading">Failed to connect to the backend. Make sure the Orion backend is running.</div>';
                        }
                    }

                    loadData();
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
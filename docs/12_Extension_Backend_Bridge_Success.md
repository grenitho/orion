import * as vscode from 'vscode';

export class ChatPanel {
    public static currentPanel: ChatPanel | undefined;
    private readonly _panel: vscode.WebviewPanel;

    private constructor(panel: vscode.WebviewPanel) {
        this._panel = panel;
        this._panel.onDidDispose(() => this.dispose(), null);
    }

    public static createOrShow() {
        const panel = vscode.window.createWebviewPanel(
            'orionChat',
            'Orion Chat',
            vscode.ViewColumn.Beside,
            { enableScripts: true }
        );
        ChatPanel.currentPanel = new ChatPanel(panel);
        ChatPanel.currentPanel._update();
    }

    private _update() {
        this._panel.webview.html = `
            <!DOCTYPE html>
            <html>
            <head><style>body { color: white; font-family: sans-serif; padding: 20px; }</style></head>
            <body>
                <h2>Orion Chat Panel</h2>
                <p>Panel UI berhasil dimuat.</p>
            </body>
            </html>
        `;
    }

    public dispose() {
        ChatPanel.currentPanel = undefined;
        this._panel.dispose();
    }
}
import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import { ActivityProvider } from './ActivityProvider';
import { orionHeaders } from '../services/authToken';
import { getSetupGuideUrl } from '../services/backendManager';

export class ChatPanel {
    public static currentPanel: ChatPanel | undefined;
    private readonly _panel: vscode.WebviewPanel;
    private readonly _activity: ActivityProvider;
    // >>> NEW: keeps the currently running request controller, so it can be aborted
    private _currentAbortController: AbortController | undefined;

    private constructor(panel: vscode.WebviewPanel, activity: ActivityProvider) {
        this._panel = panel;
        this._activity = activity;

        this._panel.onDidDispose(() => this.dispose(), null);

        this._panel.webview.onDidReceiveMessage(
            async (message) => {
                try {
                    await this._handleMessage(message);
                } catch (error) {
                    this._panel.webview.postMessage({
                        command: 'stream_error',
                        text: `Error: ${error}`
                    });
                }
            },
            null,
            []
        );
    }

    public static createOrShow(activity: ActivityProvider) {
        const panel = vscode.window.createWebviewPanel(
            'orionChat', 'Orion Chat', vscode.ViewColumn.Beside, { enableScripts: true, retainContextWhenHidden: true }
        );
        ChatPanel.currentPanel = new ChatPanel(panel, activity);
        ChatPanel.currentPanel._update();
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
                <!-- >>> UPDATE-020 adendum-3: parse-error beacon. A separate,
                     ASCII-only script tag so it survives even if the main
                     inline script fails to PARSE; it reports the exact
                     line:col of any load error to the Extension Host log
                     (command 'webviewError'). It also owns the single
                     acquireVsCodeApi() call and stashes it on window. -->
                <script>
                    window.__orionVs = acquireVsCodeApi();
                    window.addEventListener('error', function (e) {
                        try {
                            window.__orionVs.postMessage({
                                command: 'webviewError',
                                text: (e.message || 'load error') + ' @line ' + e.lineno + ':' + e.colno
                            });
                        } catch (x) { /* nothing else we can do */ }
                    }, true);
                </script>
                <!-- >>> UPDATE-020 adendum: the CDN scripts/style used to sit
                     HERE, before the inline script. A hanging CDN request
                     (proxy / offline / blackhole) blocked the HTML parser
                     and the inline script never ran - Send and the attach
                     button died SILENTLY, no console error (owner incident
                     2026-10-25). They now load AFTER the inline script (end
                     of body), so the panel always boots; markdown and
                     highlighting upgrade themselves when the CDN arrives,
                     with safe fallbacks until then. -->
                
                <style>
                    body { 
                        color: var(--vscode-foreground); 
                        background-color: var(--vscode-editor-background); 
                        font-family: var(--vscode-font-family); 
                        padding: 10px; margin: 0; 
                        display: flex; flex-direction: column; 
                        height: 100vh; box-sizing: border-box; 
                    }
                    #chat-container { 
                        flex: 1; overflow-y: auto; margin-bottom: 10px; 
                        border: 1px solid var(--vscode-panel-border); 
                        padding: 10px; border-radius: 4px; 
                        display: flex; flex-direction: column; 
                    }
                    .msg { 
                        margin: 8px 0; padding: 8px 12px; 
                        border-radius: 6px; max-width: 95%; 
                        word-wrap: break-word; line-height: 1.5; 
                    }
                    .user { 
                        background-color: var(--vscode-input-background); 
                        color: var(--vscode-input-foreground); 
                        align-self: flex-end; margin-left: auto; 
                        white-space: pre-wrap; 
                    }
                    .ai { 
                        background-color: var(--vscode-badge-background); 
                        color: var(--vscode-badge-foreground); 
                        align-self: flex-start; 
                    }
                    
                    /* 2. Custom Styles untuk Markdown & Code Blocks */
                    .ai pre { 
                        background-color: #1e1e1e; 
                        padding: 12px; border-radius: 6px; 
                        overflow-x: auto; margin: 10px 0; 
                        border: 1px solid #333;
                    }
                    .ai code { 
                        font-family: 'Consolas', 'Monaco', 'Courier New', monospace; 
                        font-size: 0.9em; 
                    }
                    .ai p { margin: 8px 0; }
                    .ai ul, .ai ol { margin: 8px 0; padding-left: 20px; }
                    .ai h1, .ai h2, .ai h3 { margin-top: 12px; margin-bottom: 8px; font-size: 1.1em; font-weight: bold; }
                    .ai a { color: var(--vscode-textLink-foreground); text-decoration: none; }
                    .ai a:hover { text-decoration: underline; }
                    .ai blockquote { border-left: 3px solid var(--vscode-textLink-foreground); padding-left: 10px; color: var(--vscode-descriptionForeground); margin: 8px 0; }

                    #input-area { display: flex; gap: 8px; }
                    textarea { 
                        flex: 1; padding: 8px; 
                        background: var(--vscode-input-background); 
                        color: var(--vscode-input-foreground); 
                        border: 1px solid var(--vscode-input-border); 
                        border-radius: 4px; resize: none; font-family: inherit; 
                    }
                    button { 
                        padding: 8px 16px; 
                        background: var(--vscode-button-background); 
                        color: var(--vscode-button-foreground); 
                        border: none; border-radius: 4px; 
                        cursor: pointer; font-weight: bold; 
                    }
                    button:disabled { opacity: 0.5; cursor: not-allowed; }
                    /* >>> NEW: cancel button style, red, only visible while processing */
                    #cancel-btn {
                        background: var(--vscode-errorForeground, #f14c4c);
                        color: white;
                        display: none;
                    }
                    #cancel-btn.visible { display: inline-block; }
                    /* >>> UPDATE-020: attachment chips above the input row.
                        Empty container collapses; chips ride the NEXT send
                        only (single-turn rule, same as the inbox). */
                    #attach-chips { display: flex; flex-wrap: wrap; gap: 6px; margin: 0 0 6px 0; }
                    #attach-chips:empty { display: none; margin: 0; }
                    .chip { display: inline-flex; align-items: center; gap: 6px; padding: 3px 8px;
                            border: 1px solid var(--vscode-panel-border, #555); border-radius: 10px;
                            font-size: 11px; color: var(--vscode-descriptionForeground);
                            background: var(--vscode-input-background); }
                    .chip button { padding: 0 5px; background: transparent; color: inherit;
                                   font-weight: normal; font-size: 12px; }
                </style>
            </head>
            <body>
                <div id="chat-container"></div>
                <div id="attach-chips"></div>
                <div id="input-area">
                    <!-- ORION-22: layout restored to the simple form. The input box uses
                         flex:1 from the #input-area CSS so it spans the full width. -->
                    <textarea id="msg-input" placeholder="Type a command... (Shift+Enter for a new line)" rows="3"
                              style="flex:1 1 auto;min-width:0;min-height:62px;"></textarea>
                    <!-- >>> UPDATE-020: the attach button revives the dormant
                         attachment forwarding (ORION-20b) with a REAL UI: the
                         host reads the files, chips show what rides on the
                         next message, backend caps still apply server-side. -->
                    <button id="attach-btn" title="Attach files (images or text/code) to the NEXT message only">📎</button>
                    <button id="send-btn">Send</button>
                    <button id="cancel-btn">Cancel</button>
                </div>
                </div>
                <script>
                    const vscode = window.__orionVs || acquireVsCodeApi();
                    // >>> UPDATE-020 adendum: boot beacon - proves in the
                    // Extension Host log that this inline script executed
                    // (the 2026-10-25 silent-death incident had no signal).
                    vscode.postMessage({ command: 'webviewBoot' });
                    const input = document.getElementById('msg-input');
                    const btn = document.getElementById('send-btn');
                    const cancelBtn = document.getElementById('cancel-btn'); // >>> NEW
                    const container = document.getElementById('chat-container');
                    let isProcessing = false;
                    let currentAiText = "";

                    // >>> UPDATE-020 adendum: CDN-independent rendering.
                    // marked/highlight.js may be absent (offline) or arrive
                    // late (they load after this script now); the panel must
                    // render either way, so both are guarded with honest
                    // fallbacks (escaped preformatted text / no coloring).
                    function escapeHtml(t) {
                        return String(t).replace(/[&<>]/g, function (c) {
                            return { '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c];
                        });
                    }
                    function renderMarkdown(text) {
                        let html;
                        if (typeof marked !== 'undefined' && marked.parse) {
                            try {
                                html = marked.parse(text);
                            } catch (e) {
                                html = '<pre style="white-space:pre-wrap">' + escapeHtml(text) + '</pre>';
                            }
                        } else {
                            html = '<pre style="white-space:pre-wrap">' + escapeHtml(text) + '</pre>';
                        }
                        // Create a temporary div to apply highlight.js
                        let tempDiv = document.createElement('div');
                        tempDiv.innerHTML = html;
                        if (typeof hljs !== 'undefined' && hljs.highlightElement) {
                            tempDiv.querySelectorAll('pre code').forEach((block) => {
                                hljs.highlightElement(block);
                            });
                        }
                        return tempDiv.innerHTML;
                            }

                    function appendMsg(text, type) {
                        const div = document.createElement('div');
                        div.className = 'msg ' + type;
                        if (type === 'user') {
                            div.innerText = text;
                        } else {
                            div.innerHTML = renderMarkdown(text);
                        }
                        container.appendChild(div);
                        container.scrollTop = container.scrollHeight;
                        return div;
                    }

                    function appendTokenToLastAi(token) {
                        let lastAi = container.querySelector('.msg.ai:last-child');
                        if (!lastAi) {
                            lastAi = document.createElement('div');
                            lastAi.className = 'msg ai';
                            container.appendChild(lastAi);
                            currentAiText = ""; // Reset the buffer for a new AI message
                        }
                        currentAiText += token;
                        // Re-render in real time while streaming
                        lastAi.innerHTML = renderMarkdown(currentAiText);
                        container.scrollTop = container.scrollHeight;
                    }

                    

                    function send() {
                        if (isProcessing) return;
                        const text = input.value.trim();
                        if (!text) return;

                        currentAiText = ""; // Reset the buffer when the user sends a new message
                        // >>> UPDATE-020: attachments ride THIS message only
                        // (single-turn rule, same as the inbox); the chips
                        // clear immediately so a later message never leaks
                        // them by accident.
                        const atts = pendingAttachments;
                        const label = atts.length
                            ? text + '\n\n\ud83d\udcce ' + atts.map(a => a.name).join(', ')
                            : text;
                        appendMsg(label, 'user');
                        input.value = '';
                        pendingAttachments = [];
                        renderChips();
                        isProcessing = true;
                        btn.disabled = true;
                        btn.innerText = 'Processing...';
                        cancelBtn.classList.add('visible');

                        vscode.postMessage({ command: 'chat', text: text, attachments: atts });
                    }

                    // >>> NEW: cancel function
                    function cancel() {
                        if (!isProcessing) return;
                        vscode.postMessage({ command: 'cancel' });
                    }

                    // >>> UPDATE-020: attachment chips. The \ud83d\udcce button asks the
                    // extension host to open a file dialog and read the files
                    // (privileged work stays out of the webview); the chips
                    // here only DISPLAY what will ride on the next send and
                    // let the user drop an item before sending.
                    let pendingAttachments = [];
                    const attachBtn = document.getElementById('attach-btn');
                    const chipsBox = document.getElementById('attach-chips');

                    function renderChips() {
                        chipsBox.innerHTML = '';
                        pendingAttachments.forEach((a, i) => {
                            const chip = document.createElement('span');
                            chip.className = 'chip';
                            const kb = Math.max(1, Math.round((a.size || 0) / 1024));
                            chip.appendChild(document.createTextNode(a.name + ' (' + kb + ' KB)'));
                            const x = document.createElement('button');
                            x.textContent = '\\u00d7';
                            x.title = 'Remove ' + a.name;
                            x.addEventListener('click', () => {
                                pendingAttachments.splice(i, 1);
                                renderChips();
                            });
                            chip.appendChild(x);
                            chipsBox.appendChild(chip);
                        });
                    }

                    attachBtn.addEventListener('click', () => {
                        vscode.postMessage({ command: 'pickAttachments' });
                    });

                    // >>> PATCH ORION-13b: render the todo list in the chat panel
                    function renderTodos(markdown, todos) {
                        let box = document.getElementById('orion-todo-box');
                        if (!box) {
                            box = document.createElement('div');
                            box.id = 'orion-todo-box';
                            box.style.cssText = 'margin:8px 0;padding:8px 10px;border-left:3px solid #0e639c;background:rgba(14,99,156,0.08);border-radius:3px;font-size:12px;';
                            const chat = document.getElementById('chat') || document.body;
                            chat.appendChild(box);
                        }
                        const selesai = (todos || []).filter(t => t.status === 'completed').length;
                        const total = (todos || []).length;
                        const ikon = { pending: '\u2b1c', in_progress: '\ud83d\udd04', completed: '\u2705' };
                        const baris = (todos || []).map(t =>
                            '<div>' + (ikon[t.status] || '\u2b1c') + ' <code>[' + t.id + ']</code> ' +
                            String(t.text).replace(/</g, '&lt;') +
                            (t.status === 'completed' ? ' <span style=\"opacity:.6\">\u2014 done</span>' : '') +
                            '</div>'
                        ).join('');
                        box.innerHTML = '<div style=\"font-weight:600;margin-bottom:4px\">Task List (' +
                            selesai + '/' + total + ')</div>' + baris;
                        box.scrollIntoView({ block: 'end', behavior: 'smooth' });
                    }

                    window.addEventListener('message', event => {
                        const msg = event.data;
                        if (msg.command === 'attachmentsPicked') {
                            // >>> UPDATE-020: host read the picked files; keep
                            // max 8 items total (backend caps images at 4 per
                            // message anyway; oversize was rejected host-side).
                            pendingAttachments = pendingAttachments
                                .concat(msg.items || [])
                                .slice(0, 8);
                            renderChips();
                        } else if (msg.command === 'stream_done') {
                            isProcessing = false;
                            btn.disabled = false;
                            btn.innerText = 'Send';
                            cancelBtn.classList.remove('visible'); // >>> NEW
                        } else if (msg.command === 'stream_error') {
                            isProcessing = false;
                            btn.disabled = false;
                            btn.innerText = 'Send';
                            cancelBtn.classList.remove('visible'); // >>> NEW
                            appendMsg('\u26a0\ufe0f **Error:** ' + msg.text, 'ai');
                        } else if (msg.command === 'stream_cancelled') {
                            // >>> NEW: cancelled by the user, not a real error
                            isProcessing = false;
                            btn.disabled = false;
                            btn.innerText = 'Send';
                            cancelBtn.classList.remove('visible');
                            appendMsg('\ud83d\uded1 Cancelled by the user.', 'ai');
                        } else if (msg.command === 'ask_user') {
                            isProcessing = false;
                            btn.disabled = false;
                            btn.innerText = 'Send';
                            cancelBtn.classList.remove('visible'); // >>> NEW
                            appendMsg('\u2753 ' + msg.question, 'ai');
                        } else if (msg.command === 'append_token') {
                            appendTokenToLastAi(msg.text);
                        }
                        // >>> PATCH ORION-13 (completing the integration chain) ========
                        // The chain was previously BROKEN at the last link:
                        //   backend sends 'todo_update'  (main.py:1531)      [present]
                        //   -> ChatPanel._processStreamData                  [MISSING -> PATCH-11]
                        //   -> postMessage to the webview                    [present]
                        //   -> webview renders                               [MISSING -> THIS PATCH]
                        // That is why the todo-tracking feature never appeared to
                        // work, even though the backend was already sending the data.
                        else if (msg.command === 'todo_update') {
                            renderTodos(msg.markdown, msg.todos);
                        }
                    });

                    btn.addEventListener('click', send);
                    cancelBtn.addEventListener('click', cancel); // >>> NEW
                    input.addEventListener('keydown', e => {
                        if (e.key === 'Enter' && !e.shiftKey) {
                            e.preventDefault();
                            send();
                        }
                    });
                </script>
                <!-- >>> UPDATE-020 adendum: CDN enhancements load LAST, so a
                     hanging request can never block the panel boot. -->
                <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/atom-one-dark.min.css">
                <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
                <script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
            </body>
            </html>
        `;
    }


    // >>> PATCH ORION-20a ================================================
    // COLLECTING ORION'S "EYES" from VS Code:
    //   1. The file the user has open    -> Orion knows where the user's focus is
    //   2. The selected text             -> the most relevant context, for free
    //   3. Problems-panel diagnostics    -> the TRUTH about current code errors
    //
    // Why diagnostics matter: until now Orion judged its edits successful
    // solely from its own tool output. But VS Code already knows exactly
    // whether that file compiles or not (red in the Problems panel). By
    // sending this to the backend, Orion can realize on its own that its edit
    // broke something, then fix it -- without being told by the user.
    // ====================================================================
    private _collectEditorContext() {
        let activeFile = '';
        let selection = '';
        const editor = vscode.window.activeTextEditor;
        if (editor) {
            try {
                activeFile = vscode.workspace.asRelativePath(editor.document.uri, false);
                if (!editor.selection.isEmpty) {
                    selection = editor.document.getText(editor.selection);
                    if (selection.length > 4000) {
                        selection = selection.slice(0, 4000) + '\n...[selection truncated]';
                    }
                }
            } catch (e) {
                // do not let a context failure break sending the message
            }
        }

        const baris: string[] = [];
        try {
            const semua = vscode.languages.getDiagnostics();
            for (const entri of semua) {
                const uri = entri[0];
                const list = entri[1];
                const rel = vscode.workspace.asRelativePath(uri, false);
                if (rel.indexOf('..') === 0) { continue; }   // di luar workspace
                for (const d of list) {
                    const sev = d.severity === vscode.DiagnosticSeverity.Error ? 'ERROR'
                        : d.severity === vscode.DiagnosticSeverity.Warning ? 'WARN' : '';
                    if (!sev) { continue; }
                    baris.push(sev + ' ' + rel + ':' + (d.range.start.line + 1) + ' ' + d.message);
                    if (baris.length >= 30) { break; }
                }
                if (baris.length >= 30) { break; }
            }
        } catch (e) {
            // same: a diagnostics failure must not break the chat
        }

        return { activeFile: activeFile, selection: selection, diagnostics: baris };
    }
    // >>> AKHIR PATCH ORION-20a ==========================================

    // >>> UPDATE-020: the 📎 picker. Caps MIRROR backend/app/services/
    // attachments.py (7 MB per image = Gemini's inline limit, 512 KB per
    // text file, 4 images per message) so an oversized file is rejected
    // HERE with a clear toast instead of burning a backend round-trip;
    // the backend re-checks anyway (defence in depth, same numbers).
    private static readonly ATTACH_MAX_IMAGE_BYTES = 7 * 1024 * 1024;
    private static readonly ATTACH_MAX_TEXT_BYTES = 512 * 1024;
    private static readonly ATTACH_MAX_IMAGES = 4;
    private static readonly ATTACH_MAX_TOTAL = 8;

    private async _pickAttachments(): Promise<void> {
        const picked = await vscode.window.showOpenDialog({
            canSelectFiles: true,
            canSelectFolders: false,
            canSelectMany: true,
            openLabel: 'Attach to next message',
            filters: {
                'Images': ['png', 'jpg', 'jpeg', 'webp'],
                'Text & code': ['txt', 'md', 'csv', 'tsv', 'log', 'json', 'py', 'ts', 'tsx', 'js', 'jsx', 'html', 'css', 'sql', 'sh', 'ps1', 'yaml', 'yml', 'toml', 'ini', 'cfg'],
            },
        });
        if (!picked || !picked.length) { return; }

        const items: Array<{ name: string; mime: string; size: number; data: string }> = [];
        const rejected: string[] = [];
        let images = 0;
        for (const uri of picked) {
            const name = uri.fsPath.split(/[\\/]/).pop() || 'file';
            try {
                const stat = await vscode.workspace.fs.stat(uri);
                const lower = name.toLowerCase();
                const isImage = /\.(png|jpe?g|webp)$/.test(lower);
                const cap = isImage
                    ? ChatPanel.ATTACH_MAX_IMAGE_BYTES
                    : ChatPanel.ATTACH_MAX_TEXT_BYTES;
                if (stat.size > cap) {
                    rejected.push(`${name}: exceeds the ${isImage ? '7 MB image' : '512 KB text'} limit`);
                    continue;
                }
                if (isImage && images >= ChatPanel.ATTACH_MAX_IMAGES) {
                    rejected.push(`${name}: more than ${ChatPanel.ATTACH_MAX_IMAGES} images per message`);
                    continue;
                }
                if (items.length >= ChatPanel.ATTACH_MAX_TOTAL) {
                    rejected.push(`${name}: more than ${ChatPanel.ATTACH_MAX_TOTAL} files per message`);
                    continue;
                }
                const bytes = await vscode.workspace.fs.readFile(uri);
                const mime = isImage
                    ? (lower.endsWith('.png') ? 'image/png'
                        : lower.endsWith('.webp') ? 'image/webp'
                            : 'image/jpeg')
                    : 'text/plain';
                if (isImage) { images++; }
                items.push({
                    name,
                    mime,
                    size: stat.size,
                    data: Buffer.from(bytes).toString('base64'),
                });
            } catch (e: unknown) {
                const msg = e instanceof Error ? e.message : String(e);
                rejected.push(`${name}: unreadable (${msg})`);
            }
        }

        if (rejected.length) {
            vscode.window.showWarningMessage(
                'Orion attach: skipped ' + rejected.length + ' file(s) — ' +
                rejected.slice(0, 3).join('; ') +
                (rejected.length > 3 ? ` (+${rejected.length - 3} more)` : '')
            );
        }
        if (items.length) {
            this._panel.webview.postMessage({ command: 'attachmentsPicked', items });
            this._activity.addActivity(`Attached ${items.length} file(s) to the next message`, '');
        }
    }
    // >>> END OF UPDATE-020 attach picker ==================================

    private async _handleMessage(message: any) {
        // >>> UPDATE-020 adendum: boot beacon from the chat webview's inline
        // script. If this line never appears in the Extension Host log while
        // the chat panel is open, the webview script did not run — the
        // exact signal the 2026-10-25 silent-death incident lacked.
        if (message.command === 'webviewBoot') {
            console.log('[ORION] chat webview inline script booted');
            return;
        }

        // >>> UPDATE-020 adendum-3: parse/load errors from the chat webview,
        // reported by the ASCII-only beacon script. These are INVISIBLE in
        // the workbench DevTools console (webview page errors are not
        // forwarded), which is exactly why the 2026-10-25 silent death took
        // three rounds to diagnose.
        if (message.command === 'webviewError') {
            console.error('[ORION] chat webview load error: ' + String(message.text));
            return;
        }

        // >>> NEW: handler for the Cancel button
        if (message.command === 'cancel') {
            if (this._currentAbortController) {
                this._activity.addActivity('Cancelling process...', '');
                this._currentAbortController.abort();
                this._currentAbortController = undefined;
            }
            return;
        }

        // >>> UPDATE-020: the 📎 button. Dialog + fs reads happen HERE (the
        // extension host), the webview only renders chips — same split as
        // the Setup Wizard and MCP panel.
        if (message.command === 'pickAttachments') {
            await this._pickAttachments();
            return;
        }

        if (message.command === 'chat') {
            this._activity.addActivity('Sending message...', message.text);
            // >>> NEW: create a new controller for this request, keep it so it can be aborted
            const controller = new AbortController();
            this._currentAbortController = controller;
            try {
                const folders = vscode.workspace.workspaceFolders;
                if (folders) {
                    // >>> UPDATE-010: a failing set-project (backend still booting,
                    // transient hiccup) must not kill the whole request before /chat
                    // even runs — /chat will surface a proper error if the backend
                    // really is down.
                    try {
                        await fetch('http://127.0.0.1:8000/set-project', {
                            method: 'POST',
                            headers: orionHeaders(),
                            body: JSON.stringify({ path: folders[0].uri.fsPath })
                        });
                    } catch (spErr) {
                        console.warn('[ORION] set-project failed (non-fatal):', spErr);
                    }
                }

                const response = await fetch('http://127.0.0.1:8000/chat', {
                    method: 'POST',
                    headers: orionHeaders(),
                    // >>> ATTACHMENT FEATURE: forward attachments from the webview to the backend
                    body: JSON.stringify({
                        message: message.text,
                        // >>> UPDATE-020: the dormant forwarding is now ALIVE:
                        // chips from the 📎 button travel as [{name,mime,size,
                        // data(base64)}]; backend/app/services/attachments.py
                        // already speaks this shape (image data-URI parts,
                        // capped text, history placeholders).
                        attachments: Array.isArray(message.attachments) ? message.attachments : [],
                        // >>> PATCH ORION-20b: include Orion's "eyes" with every request
                        editor: this._collectEditorContext()
                    }),
                    signal: controller.signal // >>> NEW: connect to the AbortController
                });

                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }

                if (!response.body) {
                    throw new Error("Response body is null");
                }

                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';

                while (true) {
                    const { done, value } = await reader.read();
                    if (done) { break; }

                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split('\n');
                    buffer = lines.pop() || '';

                    for (const line of lines) {
                        const trimmedLine = line.trim();
                        if (trimmedLine.startsWith('data: ')) {
                            const jsonStr = trimmedLine.substring(6).trim();
                            if (jsonStr === '[DONE]' || jsonStr === '') { continue; }
                            try {
                                const data = JSON.parse(jsonStr);
                                this._processStreamData(data);
                            } catch (e) {
                                console.error('Parse error:', e, 'on line:', jsonStr);
                            }
                        }
                    }
                }

                // >>> PATCH ORION-12 (CRITICAL): previously, EVERY time the reader closed
                // the UI immediately reported 'Finished'/'Success' -- including when the
                // backend died halfway (kill, crash, stdout backpressure). The user saw
                // "Success" while the work had stopped halfway. Now it is distinguished:
                // it only counts as success when the backend really sent a completion signal.
                if (this._sawTerminalEvent) {
                    this._activity.addActivity('Finished', 'Success');
                    this._panel.webview.postMessage({ command: 'stream_done' });
                } else {
                    const msg = 'Connection to the backend was lost before the work finished. ' +
                        'Check .orion/logs/backend.log for details.';
                    this._activity.addActivity('Disconnected', msg);
                    this._panel.webview.postMessage({ command: 'stream_error', text: msg });
                }

            } catch (e: any) {
                // >>> NEW: distinguish a manual cancellation (AbortError) from a real error
                if (e.name === 'AbortError') {
                    this._activity.addActivity('Cancelled', 'User cancelled the process');
                    this._panel.webview.postMessage({ command: 'stream_cancelled' });
                } else {
                    // >>> UPDATE-010: raw driver errors ("fetch failed") are noise to
                    // end users; translate backend-unreachable into concrete next steps.
                    const text = this._describeError(e);
                    this._activity.addActivity('Error', text);
                    this._panel.webview.postMessage({ command: 'stream_error', text: text });
                }
            } finally {
                // >>> NEW: clear the controller reference after the request finishes/is cancelled/errors
                this._currentAbortController = undefined;
                this._sawTerminalEvent = false;   // >>> PATCH ORION-12c: reset for the next run
            }
        }
    }

    // >>> REPLACE THIS METHOD <<<
    // >>> PATCH ORION-12b: flag for whether the backend sent a legitimate completion signal
    private _sawTerminalEvent: boolean = false;

    // >>> UPDATE-010: map low-level connection failures onto guidance.
    // Node's fetch (undici) throws a TypeError with message "fetch failed"
    // when the backend is down; the raw text tells a beginner nothing.
    private _describeError(e: any): string {
        const raw = String(e?.message || e);
        const isConnectionError = e instanceof TypeError ||
            /fetch failed|ECONNREFUSED|ECONNRESET|EPIPE|socket hang up/i.test(raw);
        if (!isConnectionError) {
            return raw;
        }
        const guide = getSetupGuideUrl() || 'docs/PANDUAN_SETUP.md in the Orion repository';
        return [
            '**Cannot reach the Orion backend** at `http://127.0.0.1:8000`.',
            '',
            'The chat needs Orion\'s local Python backend running. Quick fixes:',
            '1. `Ctrl+Shift+P` → **Orion: Start Backend** — wait for the "Backend started automatically" notification, then send your message again.',
            `2. First time on this machine? One-time setup guide (incl. one-line Windows installer): ${guide}`,
            '3. Still failing? Check that Python 3.11+ is installed and that port 8000 is free.'
        ].join('\n');
    }

    private async _processStreamData(data: any) {
        if (data.type === 'token') {
            this._panel.webview.postMessage({ command: 'append_token', text: data.text });
        } else if (data.type === 'status') {
            this._activity.addActivity(data.text, '...');
        } else if (data.type === 'ask_user') {
            this._panel.webview.postMessage({ command: 'ask_user', question: data.question });
        }
        else if (data.type === 'confirm_terminal') {
            await this._handleTerminalConfirmation(data.command);
        }
        else if (data.type === 'error') {
            this._panel.webview.postMessage({ command: 'stream_error', text: data.text });
        }
        // >>> PATCH ORION-10 (CRITICAL): the backend sends {'type':'done'} as the
        // legitimate completion signal, but it was NOT handled at all before. The extension
        // only relied on the reader closing. As a result, when the backend sent 'error'
        // and then closed the stream, the UI could show "Finished/Success" for a failed run.
        else if (data.type === 'done') {
            this._sawTerminalEvent = true;
            this._panel.webview.postMessage({ command: 'stream_done' });
        }
        // >>> PATCH ORION-11: the backend has been sending 'todo_update' (main.py:1531)
        // for a long time, but the UI never handled it -> the todo list never appeared.
        // This is why todo tracking looked "broken" even though the backend was running.
        else if (data.type === 'todo_update') {
            this._panel.webview.postMessage({
                command: 'todo_update',
                markdown: data.markdown,
                todos: data.todos
            });
        }
    }

    // >>> ADDED THESE 2 METHODS BEFORE dispose() <<<

    private async _handleTerminalConfirmation(command: string) {
        // 1. Show the modal pop-up in VS Code
        const choice = await vscode.window.showWarningMessage(
            `Orion wants to run a risky command:\n\n${command}\n\nDo you allow it?`,
            { modal: true },
            'Approve', 'Reject'
        );

        let feedbackMessage = "";

        // 2. Process the user's choice
        if (choice === 'Approve') {
            try {
                // Call the new backend endpoint for execution
                const res = await fetch('http://127.0.0.1:8000/execute-terminal', {
                    method: 'POST',
                    headers: orionHeaders(),
                    body: JSON.stringify({ command: command })
                });
                const result: any = await res.json();

                if (result.success) {
                    feedbackMessage = `[SYSTEM: User approved the dangerous command. Execution output:\n${result.output}]\nPlease continue with your task.`;
                } else {
                    feedbackMessage = `[SYSTEM: User approved, but backend execution failed: ${result.output}]`;
                }
            } catch (err: any) {
                feedbackMessage = `[SYSTEM: User approved, but backend execution failed: ${err.message}]`;
            }
        } else {
            // If the user clicks Reject (or closes the pop-up)
            feedbackMessage = `[SYSTEM: User REJECTED the dangerous command. Do not run it. Please adjust your plan or ask for an alternative.]`;
        }

        // 3. Show the status in the Chat Panel so the user knows what happened
        const statusText = choice === 'Approve' ? 'Command approved and executed.' : 'Command rejected by the user.';
        this._panel.webview.postMessage({ command: 'append_token', text: `\n\n**[System: ${statusText}]**\n\n` });

        // 4. Send the feedback back to the backend so Orion can continue the agentic loop
        await this._sendFeedbackToOrion(feedbackMessage);
    }

    private async _sendFeedbackToOrion(message: string) {
        this._activity.addActivity('Resuming after terminal confirmation...', message);

        // >>> NEW: attach an AbortController on this path too, so the Cancel button
        // keeps working even mid-continuation after the terminal confirmation.
        const controller = new AbortController();
        this._currentAbortController = controller;

        try {
            // Send the feedback message to the /chat endpoint
            const response = await fetch('http://127.0.0.1:8000/chat', {
                method: 'POST',
                headers: orionHeaders(),
                body: JSON.stringify({ message: message }),
                signal: controller.signal
            });

            if (!response.ok || !response.body) {
                throw new Error("Failed to send feedback to the backend");
            }

            // Read the response stream from the backend (exactly like in _handleMessage)
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) { break; }

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop() || '';

                for (const line of lines) {
                    const trimmedLine = line.trim();
                    if (trimmedLine.startsWith('data: ')) {
                        const jsonStr = trimmedLine.substring(6).trim();
                        if (jsonStr === '[DONE]' || jsonStr === '') { continue; }
                        try {
                            const data = JSON.parse(jsonStr);
                            await this._processStreamData(data); // Process the next stream chunk
                        } catch (e) {
                            console.error('Parse error:', e);
                        }
                    }
                }
            }
            this._panel.webview.postMessage({ command: 'stream_done' });
        } catch (e: any) {
            if (e.name === 'AbortError') {
                this._panel.webview.postMessage({ command: 'stream_cancelled' });
            } else {
                // >>> UPDATE-010: same friendly guidance as the main chat path
                this._panel.webview.postMessage({ command: 'stream_error', text: this._describeError(e) });
            }
        } finally {
            this._currentAbortController = undefined;
        }
    }

    public dispose() {
        ChatPanel.currentPanel = undefined;
        this._panel.dispose();
    }
}
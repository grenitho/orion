import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import { ActivityProvider } from './ActivityProvider';

export class ChatPanel {
    public static currentPanel: ChatPanel | undefined;
    private readonly _panel: vscode.WebviewPanel;
    private readonly _activity: ActivityProvider;
    // >>> BARU: menyimpan controller request yang sedang berjalan, supaya bisa di-abort
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
                <!-- 1. Load Markdown & Syntax Highlighting dari CDN -->
                <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
                <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/atom-one-dark.min.css">
                <script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
                
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
                    /* >>> BARU: style tombol cancel, merah, hanya terlihat saat processing */
                    #cancel-btn {
                        background: var(--vscode-errorForeground, #f14c4c);
                        color: white;
                        display: none;
                    }
                    #cancel-btn.visible { display: inline-block; }
                </style>
            </head>
            <body>
                <div id="chat-container"></div>
                <div id="input-area">
                    <!-- ORION-22: layout dikembalikan sederhana. Kotak ketik memakai
                         flex:1 dari CSS #input-area sehingga lebarnya penuh. -->
                    <textarea id="msg-input" placeholder="Type a command... (Shift+Enter for a new line)" rows="3"
                              style="flex:1 1 auto;min-width:0;min-height:62px;"></textarea>
                    <button id="send-btn">Send</button>
                    <button id="cancel-btn">Cancel</button>
                </div>
                </div>
                <script>
                    const vscode = acquireVsCodeApi();
                    const input = document.getElementById('msg-input');
                    const btn = document.getElementById('send-btn');
                    const cancelBtn = document.getElementById('cancel-btn'); // >>> BARU
                    const container = document.getElementById('chat-container');
                    let isProcessing = false;
                    let currentAiText = "";

                    // Fungsi untuk merender pesan dengan Markdown & Highlight
                    function renderMarkdown(text) {
                        let html = marked.parse(text);
                        // Buat temporary div untuk apply highlight.js
                        let tempDiv = document.createElement('div');
                        tempDiv.innerHTML = html;
                        tempDiv.querySelectorAll('pre code').forEach((block) => {
                            hljs.highlightElement(block);
                        });
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
                            currentAiText = ""; // Reset buffer untuk pesan AI baru
                        }
                        currentAiText += token;
                        // Render ulang secara real-time saat streaming
                        lastAi.innerHTML = renderMarkdown(currentAiText);
                        container.scrollTop = container.scrollHeight;
                    }

                    

                    function send() {
                        if (isProcessing) return;
                        const text = input.value.trim();
                        if (!text) return;

                        currentAiText = ""; // Reset buffer saat user mengirim pesan baru
                        appendMsg(text, 'user');
                        input.value = '';
                        isProcessing = true;
                        btn.disabled = true;
                        btn.innerText = 'Processing...';
                        cancelBtn.classList.add('visible');

                        vscode.postMessage({ command: 'chat', text: text });
                    }

                    // >>> BARU: fungsi cancel
                    function cancel() {
                        if (!isProcessing) return;
                        vscode.postMessage({ command: 'cancel' });
                    }

                    // >>> PATCH ORION-13b: render daftar todo di panel chat
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
                        if (msg.command === 'stream_done') {
                            isProcessing = false;
                            btn.disabled = false;
                            btn.innerText = 'Send';
                            cancelBtn.classList.remove('visible'); // >>> BARU
                        } else if (msg.command === 'stream_error') {
                            isProcessing = false;
                            btn.disabled = false;
                            btn.innerText = 'Send';
                            cancelBtn.classList.remove('visible'); // >>> BARU
                            appendMsg('⚠️ **Error:** ' + msg.text, 'ai');
                        } else if (msg.command === 'stream_cancelled') {
                            // >>> BARU: dibatalkan user, bukan error sungguhan
                            isProcessing = false;
                            btn.disabled = false;
                            btn.innerText = 'Send';
                            cancelBtn.classList.remove('visible');
                            appendMsg('🛑 Cancelled by the user.', 'ai');
                        } else if (msg.command === 'ask_user') {
                            isProcessing = false;
                            btn.disabled = false;
                            btn.innerText = 'Send';
                            cancelBtn.classList.remove('visible'); // >>> BARU
                            appendMsg('❓ ' + msg.question, 'ai');
                        } else if (msg.command === 'append_token') {
                            appendTokenToLastAi(msg.text);
                        }
                        // >>> PATCH ORION-13 (melengkapi rantai integrasi) ========
                        // Rantainya sebelumnya PUTUS di mata terakhir:
                        //   backend kirim 'todo_update'  (main.py:1531)      [ada]
                        //   -> ChatPanel._processStreamData                  [TIDAK ada -> PATCH-11]
                        //   -> postMessage ke webview                        [ada]
                        //   -> webview merender                              [TIDAK ada -> PATCH INI]
                        // Itulah sebabnya fitur todo tracking tidak pernah terlihat
                        // bekerja, padahal backend-nya sudah mengirim datanya.
                        else if (msg.command === 'todo_update') {
                            renderTodos(msg.markdown, msg.todos);
                        }
                    });

                    btn.addEventListener('click', send);
                    cancelBtn.addEventListener('click', cancel); // >>> BARU
                    input.addEventListener('keydown', e => {
                        if (e.key === 'Enter' && !e.shiftKey) {
                            e.preventDefault();
                            send();
                        }
                    });
                </script>
            </body>
            </html>
        `;
    }


    // >>> PATCH ORION-20a ================================================
    // MENGUMPULKAN "MATA" ORION dari VS Code:
    //   1. File yang sedang dibuka user  -> Orion tahu fokus user di mana
    //   2. Teks yang sedang disorot       -> konteks paling relevan, gratis
    //   3. Diagnostics panel Problems     -> KEBENARAN tentang error kode saat ini
    //
    // Kenapa diagnostics penting: selama ini Orion menilai editannya berhasil
    // hanya dari output tool-nya sendiri. Padahal VS Code sudah tahu persis
    // apakah file itu compiles atau tidak (merah di panel Problems). Dengan
    // mengirimkannya ke backend, Orion bisa menyadari sendiri bahwa editannya
    // merusak sesuatu, lalu memperbaikinya -- tanpa disuruh user.
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
                // jangan biarkan kegagalan konteks menggagalkan pengiriman pesan
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
            // sama: kegagalan diagnostics tidak boleh menggagalkan chat
        }

        return { activeFile: activeFile, selection: selection, diagnostics: baris };
    }
    // >>> AKHIR PATCH ORION-20a ==========================================

    private async _handleMessage(message: any) {
        // >>> BARU: handler untuk tombol Cancel
        if (message.command === 'cancel') {
            if (this._currentAbortController) {
                this._activity.addActivity('Cancelling process...', '');
                this._currentAbortController.abort();
                this._currentAbortController = undefined;
            }
            return;
        }

        if (message.command === 'chat') {
            this._activity.addActivity('Sending message...', message.text);
            // >>> BARU: buat controller baru untuk request ini, simpan supaya bisa di-abort
            const controller = new AbortController();
            this._currentAbortController = controller;
            try {
                const folders = vscode.workspace.workspaceFolders;
                if (folders) {
                    await fetch('http://127.0.0.1:8000/set-project', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ path: folders[0].uri.fsPath })
                    });
                }

                const response = await fetch('http://127.0.0.1:8000/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    // >>> FITUR LAMPIRAN: teruskan attachments dari webview ke backend
                    body: JSON.stringify({
                        message: message.text,
                        // >>> PATCH ORION-20b: ikutkan "mata" Orion tiap request
                        editor: this._collectEditorContext()
                    }),
                    signal: controller.signal // >>> BARU: hubungkan ke AbortController
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

                // >>> PATCH ORION-12 (KRITIS): sebelumnya, SETIAP kali reader menutup
                // UI langsung melapor 'Selesai'/'Sukses' -- termasuk kalau backend
                // mati di tengah jalan (kill, crash, backpressure stdout). User melihat
                // "Sukses" padahal pekerjaan berhenti separuh. Sekarang dibedakan:
                // hanya dianggap sukses kalau backend benar-benar mengirim sinyal selesai.
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
                // >>> BARU: bedakan pembatalan manual (AbortError) dari error sungguhan
                if (e.name === 'AbortError') {
                    this._activity.addActivity('Cancelled', 'User cancelled the process');
                    this._panel.webview.postMessage({ command: 'stream_cancelled' });
                } else {
                    this._activity.addActivity('Error', e.message);
                    this._panel.webview.postMessage({ command: 'stream_error', text: e.message });
                }
            } finally {
                // >>> BARU: bersihkan referensi controller setelah request selesai/dibatalkan/error
                this._currentAbortController = undefined;
                this._sawTerminalEvent = false;   // >>> PATCH ORION-12c: reset untuk run berikutnya
            }
        }
    }

    // >>> GANTI METHOD INI <<<
    // >>> PATCH ORION-12b: penanda apakah backend mengirim sinyal selesai yang sah
    private _sawTerminalEvent: boolean = false;

    private async _processStreamData(data: any) {
        if (data.type === 'token') {
            this._panel.webview.postMessage({ command: 'append_token', text: data.text });
        } else if (data.type === 'status') {
            this._activity.addActivity(data.text, '...');
        } else if (data.type === 'ask_user') {
            this._panel.webview.postMessage({ command: 'ask_user', question: data.question });
        }
        // >>> TAMBAHKAN BLOK INI <<<
        else if (data.type === 'confirm_terminal') {
            await this._handleTerminalConfirmation(data.command);
        }
        // >>> AKHIR TAMBAHAN <<<
        else if (data.type === 'error') {
            this._panel.webview.postMessage({ command: 'stream_error', text: data.text });
        }
        // >>> PATCH ORION-10 (KRITIS): backend mengirim {'type':'done'} sebagai sinyal
        // selesai yang sah, tapi sebelumnya TIDAK ditangani sama sekali. Extension hanya
        // mengandalkan reader menutup. Akibatnya kalau backend mengirim 'error' lalu
        // menutup stream, UI bisa menampilkan "Selesai/Sukses" untuk run yang gagal.
        else if (data.type === 'done') {
            this._sawTerminalEvent = true;
            this._panel.webview.postMessage({ command: 'stream_done' });
        }
        // >>> PATCH ORION-11: backend sudah mengirim 'todo_update' (main.py:1531) sejak
        // lama, tapi UI tidak pernah menanganinya -> daftar todo tidak pernah tampil.
        // Ini sebabnya fitur todo tracking terlihat "tidak bekerja" padahal backend jalan.
        else if (data.type === 'todo_update') {
            this._panel.webview.postMessage({
                command: 'todo_update',
                markdown: data.markdown,
                todos: data.todos
            });
        }
    }

    // >>> TAMBAHKAN 2 METHOD INI SEBELUM dispose() <<<

    private async _handleTerminalConfirmation(command: string) {
        // 1. Munculkan Pop-up Modal di VS Code
        const choice = await vscode.window.showWarningMessage(
            `Orion wants to run a risky command:\n\n${command}\n\nDo you allow it?`,
            { modal: true },
            'Approve', 'Reject'
        );

        let feedbackMessage = "";

        // 2. Proses pilihan user
        if (choice === 'Approve') {
            try {
                // Panggil endpoint baru di backend untuk eksekusi
                const res = await fetch('http://127.0.0.1:8000/execute-terminal', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
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
            // Jika user klik Reject (atau menutup pop-up)
            feedbackMessage = `[SYSTEM: User REJECTED the dangerous command. Do not run it. Please adjust your plan or ask for an alternative.]`;
        }

        // 3. Tampilkan status di Chat Panel agar user tahu apa yang terjadi
        const statusText = choice === 'Approve' ? 'Command approved and executed.' : 'Command rejected by the user.';
        this._panel.webview.postMessage({ command: 'append_token', text: `\n\n**[System: ${statusText}]**\n\n` });

        // 4. Kirim feedback kembali ke backend agar Orion bisa melanjutkan agentic loop
        await this._sendFeedbackToOrion(feedbackMessage);
    }

    private async _sendFeedbackToOrion(message: string) {
        this._activity.addActivity('Resuming after terminal confirmation...', message);

        // >>> BARU: pasang AbortController juga di jalur ini, supaya tombol Cancel
        // tetap berfungsi walau sedang di tengah proses lanjutan pasca-konfirmasi terminal.
        const controller = new AbortController();
        this._currentAbortController = controller;

        try {
            // Kirim pesan feedback ke endpoint /chat
            const response = await fetch('http://127.0.0.1:8000/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: message }),
                signal: controller.signal
            });

            if (!response.ok || !response.body) {
                throw new Error("Failed to send feedback to the backend");
            }

            // Baca stream response dari backend (sama persis seperti di _handleMessage)
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
                            await this._processStreamData(data); // Proses stream selanjutnya
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
                this._panel.webview.postMessage({ command: 'stream_error', text: e.message });
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
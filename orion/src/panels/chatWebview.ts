// @ts-nocheck
/* Orion chat webview runtime — bundled to dist/chatwebview.js
   (UPDATE-020 adendum-4). External file instead of one giant inline script:
   VS Code delivers webview HTML through document.write(), and an inline
   script can be split/re-encoded by that delivery path (owner incident
   2026-10-25, VS Code 1.143). An external script parses from its own file
   URL and is immune to it. */

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

                    // >>> UPDATE-022 Commit 3: per-bubble Copy button. The
                    // owner was screenshotting answers because the raw text
                    // could not be taken out of the panel. Every bubble is
                    // now a bar (Copy) + a .msg-body; streaming re-renders
                    // ONLY the body, so the button never flickers away, and
                    // the RAW pre-markdown text stays on the bubble
                    // (.__orionRaw) — Copy returns exactly what the backend
                    // sent, not a DOM-reconstructed approximation.
                    function copyFeedback(btn, ok) {
                        btn.textContent = ok ? '\u2713 Copied' : 'Copy failed';
                        setTimeout(function () { btn.textContent = 'Copy'; }, 1400);
                    }
                    function copyViaExecCommand(text, btn) {
                        // Fallback for webviews where navigator.clipboard is
                        // unavailable or rejects (focus/permission quirks).
                        try {
                            const ta = document.createElement('textarea');
                            ta.value = text;
                            ta.style.cssText = 'position:fixed;top:-1000px;left:0;opacity:0';
                            document.body.appendChild(ta);
                            ta.focus();
                            ta.select();
                            const ok = document.execCommand('copy');
                            document.body.removeChild(ta);
                            copyFeedback(btn, ok);
                        } catch (e) {
                            copyFeedback(btn, false);
                        }
                    }
                    function copyBubble(bubble, btn) {
                        const body = bubble.querySelector('.msg-body');
                        const raw = (typeof bubble.__orionRaw === 'string' && bubble.__orionRaw !== '')
                            ? bubble.__orionRaw
                            : ((body || bubble).innerText || '');
                        if (navigator.clipboard && navigator.clipboard.writeText) {
                            navigator.clipboard.writeText(raw).then(
                                function () { copyFeedback(btn, true); },
                                function () { copyViaExecCommand(raw, btn); }
                            );
                        } else {
                            copyViaExecCommand(raw, btn);
                        }
                    }
                    function makeBubble(type) {
                        const div = document.createElement('div');
                        div.className = 'msg ' + type;
                        div.__orionRaw = '';
                        const bar = document.createElement('div');
                        bar.className = 'msg-bar';
                        const copyBtn = document.createElement('button');
                        copyBtn.className = 'copy-btn';
                        copyBtn.textContent = 'Copy';
                        copyBtn.title = 'Copy the raw text of this message';
                        copyBtn.addEventListener('click', function () { copyBubble(div, copyBtn); });
                        bar.appendChild(copyBtn);
                        const body = document.createElement('div');
                        body.className = 'msg-body';
                        div.appendChild(bar);
                        div.appendChild(body);
                        return div;
                    }

                    function appendMsg(text, type) {
                        const div = makeBubble(type);
                        const body = div.querySelector('.msg-body');
                        div.__orionRaw = text;
                        if (type === 'user') {
                            body.innerText = text;
                        } else {
                            body.innerHTML = renderMarkdown(text);
                        }
                        container.appendChild(div);
                        container.scrollTop = container.scrollHeight;
                        return div;
                    }

                    function appendTokenToLastAi(token) {
                        let lastAi = container.querySelector('.msg.ai:last-child');
                        if (!lastAi) {
                            lastAi = makeBubble('ai');
                            container.appendChild(lastAi);
                            currentAiText = ""; // Reset the buffer for a new AI message
                        }
                        let body = lastAi.querySelector('.msg-body');
                        if (!body) {
                            // Defensive: only reachable if a bubble from an
                            // older shape survived; never crash the stream.
                            body = document.createElement('div');
                            body.className = 'msg-body';
                            lastAi.appendChild(body);
                        }
                        currentAiText += token;
                        lastAi.__orionRaw = currentAiText;
                        // Re-render in real time while streaming — the body
                        // only, so the Copy button in the bar stays put.
                        body.innerHTML = renderMarkdown(currentAiText);
                        container.scrollTop = container.scrollHeight;
                    }

                    

                    function send() {
                        if (isProcessing) {return;}
                        const text = input.value.trim();
                        if (!text) {return;}

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
                        if (!isProcessing) {return;}
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
                
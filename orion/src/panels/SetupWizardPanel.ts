import * as vscode from 'vscode';
import * as cp from 'child_process';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import * as https from 'https';
import {
    detectEnvironment,
    findVenvPython,
    getRepoBaseUrl,
    getRepoZipUrl,
    getSetupGuideUrl,
    restartBackend,
} from '../services/backendManager';

// ===========================================================================
// >>> UPDATE-012: "Route 1" from docs/PANDUAN_SETUP.md — an in-VS Code Setup
// Wizard that installs the local backend WITHOUT a terminal:
//   1. detect Python (offer a one-click winget install on Windows)
//   2. download the backend from GitHub (zip, no git required)
//   3. create .venv + pip install with live progress
//   4. API-key form -> writes backend/.env (multi Gemini keys + optional
//      PROVIDER_<N>_* groups; stored keys are shown masked, never echoed)
//   5. restart the backend and open the chat.
// The extension host does ALL privileged work (spawn/fs/network); the
// webview is a thin UI talking over postMessage with a strict CSP.
// ===========================================================================

interface RunResult { code: number; out: string; }

interface PythonInfo { cmd: string; version: string; ok: boolean; }

interface ScannedProvider { n: number; name: string; maskedKey: string; baseUrl: string; models: string; }

interface ScannedEnv {
    exists: boolean;
    path: string;
    geminiKeys: Array<{ index: number; masked: string }>;
    defaultModel: string;
    providers: ScannedProvider[];
}

interface RawProvider { name: string; apiKey: string; baseUrl: string; models: string; temperature: string; }

/** Run a process, streaming every output line to `onLine` as it arrives. */
function runProcess(
    cmd: string,
    args: string[],
    opts: { cwd?: string; env?: NodeJS.ProcessEnv; timeoutMs?: number },
    onLine?: (line: string) => void
): Promise<RunResult> {
    return new Promise((resolve) => {
        let child: cp.ChildProcess;
        try {
            child = cp.spawn(cmd, args, {
                cwd: opts.cwd,
                env: opts.env || process.env,
                windowsHide: true,
            });
        } catch (e: unknown) {
            const msg = e instanceof Error ? e.message : String(e);
            resolve({ code: -1, out: 'spawn failed: ' + msg });
            return;
        }
        const outLines: string[] = [];
        let settled = false;
        let timer: NodeJS.Timeout | undefined;
        const finish = (code: number) => {
            if (settled) { return; }
            settled = true;
            if (timer) { clearTimeout(timer); }
            resolve({ code, out: outLines.join('\n') });
        };
        if (opts.timeoutMs) {
            timer = setTimeout(() => {
                try { child.kill(); } catch { /* already gone */ }
                finish(-2);
            }, opts.timeoutMs);
        }
        const feeder = () => {
            let buf = '';
            return (data: Buffer) => {
                buf += data.toString();
                const parts = buf.split(/\r?\n/);
                buf = parts.pop() || '';
                for (const p of parts) {
                    const line = p.replace(/\r$/, '').trimEnd();
                    if (line) {
                        outLines.push(line);
                        if (onLine) { onLine(line); }
                    }
                }
            };
        };
        child.stdout?.on('data', feeder());
        child.stderr?.on('data', feeder());
        child.on('error', (err) => {
            const line = 'cannot run "' + cmd + '": ' + err.message;
            outLines.push(line);
            if (onLine) { onLine(line); }
            finish(-1);
        });
        child.on('close', (code) => finish(code === null ? -1 : code));
    });
}

async function detectPythonAll(envOverride?: NodeJS.ProcessEnv): Promise<{ all: PythonInfo[]; best: string }> {
    const candidates: Array<[string, string[]]> = process.platform === 'win32'
        ? [['py', ['-3']], ['python', []], ['python3', []]]
        : [['python3', []], ['python', []]];
    const all: PythonInfo[] = [];
    for (const [cmd, args] of candidates) {
        const r = await runProcess(cmd, [...args, '--version'], { timeoutMs: 6000, env: envOverride });
        const m = r.out.match(/Python (\d+)\.(\d+)(?:\.(\d+))?/);
        if (m) {
            const major = parseInt(m[1], 10);
            const minor = parseInt(m[2], 10);
            all.push({
                cmd: [cmd, ...args].join(' '),
                version: m[1] + '.' + m[2] + (m[3] ? '.' + m[3] : ''),
                ok: major === 3 && minor >= 11,
            });
        }
    }
    const best = all.find(p => p.ok);
    return { all, best: best ? best.cmd : '' };
}

function maskSecret(v: string): string {
    if (!v) { return ''; }
    return v.length > 12 ? v.slice(0, 6) + '\u2026' + v.slice(-4) : '\u2022\u2022\u2022\u2022\u2022\u2022';
}

function parseEnvText(text: string): {
    gemini: Map<number, string>;
    providers: Map<number, RawProvider>;
    defaultModel: string;
} {
    const gemini = new Map<number, string>();
    const providers = new Map<number, RawProvider>();
    let defaultModel = '';
    for (const raw of text.split(/\r?\n/)) {
        const line = raw.trim();
        if (!line || line.startsWith('#')) { continue; }
        const eq = line.indexOf('=');
        if (eq < 0) { continue; }
        const key = line.slice(0, eq).trim();
        const value = line.slice(eq + 1).trim();
        const gk = key.match(/^GEMINI_API_KEY_(\d+)$/);
        if (gk) {
            if (value) { gemini.set(parseInt(gk[1], 10), value); }
            continue;
        }
        if (key === 'DEFAULT_MODEL') { defaultModel = value; continue; }
        const pk = key.match(/^PROVIDER_(\d+)_(NAME|API_KEY|BASE_URL|MODELS|TEMPERATURE)$/);
        if (pk) {
            const n = parseInt(pk[1], 10);
            let g = providers.get(n);
            if (!g) { g = { name: '', apiKey: '', baseUrl: '', models: '', temperature: '' }; providers.set(n, g); }
            if (pk[2] === 'NAME') { g.name = value; }
            else if (pk[2] === 'API_KEY') { g.apiKey = value; }
            else if (pk[2] === 'BASE_URL') { g.baseUrl = value; }
            else if (pk[2] === 'MODELS') { g.models = value; }
            else { g.temperature = value; }
        }
    }
    return { gemini, providers, defaultModel };
}

function scanEnvFile(backendDir: string): ScannedEnv {
    const envPath = path.join(backendDir, '.env');
    const result: ScannedEnv = {
        exists: fs.existsSync(envPath),
        path: envPath,
        geminiKeys: [],
        defaultModel: '',
        providers: [],
    };
    if (!result.exists) { return result; }
    try {
        const parsed = parseEnvText(fs.readFileSync(envPath, 'utf-8'));
        for (const idx of [...parsed.gemini.keys()].sort((a, b) => a - b)) {
            result.geminiKeys.push({ index: idx, masked: maskSecret(parsed.gemini.get(idx) || '') });
        }
        result.defaultModel = parsed.defaultModel;
        for (const n of [...parsed.providers.keys()].sort((a, b) => a - b)) {
            const g = parsed.providers.get(n) as RawProvider;
            result.providers.push({ n, name: g.name, maskedKey: maskSecret(g.apiKey), baseUrl: g.baseUrl, models: g.models });
        }
    } catch {
        result.exists = false; // unreadable -> treat as absent, never crash the wizard
    }
    return result;
}

function downloadFile(url: string, dest: string, onLine: (l: string) => void): Promise<void> {
    return new Promise((resolve, reject) => {
        const doRequest = (currentUrl: string, redirectsLeft: number) => {
            https.get(currentUrl, { headers: { 'User-Agent': 'orion-setup-wizard' } }, (res) => {
                const status = res.statusCode || 0;
                if (status >= 300 && status < 400 && res.headers.location && redirectsLeft > 0) {
                    res.resume();
                    doRequest(res.headers.location, redirectsLeft - 1);
                    return;
                }
                if (status !== 200) {
                    res.resume();
                    reject(new Error('download failed with HTTP ' + status));
                    return;
                }
                const total = parseInt(res.headers['content-length'] || '0', 10);
                let received = 0;
                let lastPct = -10;
                const file = fs.createWriteStream(dest);
                res.on('data', (chunk: Buffer) => {
                    received += chunk.length;
                    if (total) {
                        const pct = Math.floor(received * 100 / total);
                        if (pct - lastPct >= 10) {
                            lastPct = pct;
                            onLine('downloading\u2026 ' + pct + '% (' + (received / 1048576).toFixed(1) + ' MB)');
                        }
                    }
                });
                res.on('error', reject);
                file.on('error', (e) => { fs.rmSync(dest, { force: true }); reject(e); });
                file.on('finish', () => file.close(() => resolve()));
                res.pipe(file);
            }).on('error', reject);
        };
        doRequest(url, 5);
    });
}

function getNonce(): string {
    let text = '';
    const possible = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
    for (let i = 0; i < 32; i++) { text += possible.charAt(Math.floor(Math.random() * possible.length)); }
    return text;
}

export class SetupWizardPanel {
    public static currentPanel: SetupWizardPanel | undefined;
    public static readonly viewType = 'orionSetupWizard';

    private readonly panel: vscode.WebviewPanel;
    private disposables: vscode.Disposable[] = [];
    private busy = false;
    private installDir: string = path.join(os.homedir(), 'orion');
    private freshPathEnv: NodeJS.ProcessEnv | undefined;

    public static createOrShow(extensionUri: vscode.Uri) {
        const column = vscode.window.activeTextEditor?.viewColumn ?? vscode.ViewColumn.One;
        if (SetupWizardPanel.currentPanel) {
            SetupWizardPanel.currentPanel.panel.reveal(column);
            return;
        }
        const panel = vscode.window.createWebviewPanel(
            SetupWizardPanel.viewType,
            'Orion Setup Wizard',
            column,
            {
                enableScripts: true,
                retainContextWhenHidden: true,
                localResourceRoots: [extensionUri],
            }
        );
        SetupWizardPanel.currentPanel = new SetupWizardPanel(panel);
    }

    private constructor(panel: vscode.WebviewPanel) {
        this.panel = panel;
        this.panel.webview.html = this.getHtml(this.panel.webview);
        this.panel.webview.onDidReceiveMessage(
            (msg: { cmd: string; [k: string]: unknown }) => { void this.onMessage(msg); },
            undefined,
            this.disposables
        );
        this.panel.onDidDispose(() => this.dispose(), null, this.disposables);
        // Push the first state as soon as the webview says it is ready.
    }

    public dispose() {
        SetupWizardPanel.currentPanel = undefined;
        this.panel.dispose();
        for (const d of this.disposables) { d.dispose(); }
        this.disposables = [];
    }

    private post(msg: Record<string, unknown>) {
        void this.panel.webview.postMessage(msg);
    }

    // ------------------------------------------------------------------ state

    private async collectState() {
        const backend = await detectEnvironment();
        const python = await detectPythonAll(this.freshPathEnv);
        let winget = false;
        if (process.platform === 'win32') {
            const r = await runProcess('winget', ['--version'], { timeoutMs: 5000, env: this.freshPathEnv });
            winget = r.code === 0;
        }
        return {
            platform: process.platform,
            python,
            winget,
            installDir: this.installDir,
            backend,
            envFile: backend.backendPath ? scanEnvFile(backend.backendPath) : null,
            repoZipUrl: getRepoZipUrl(),
            guideUrl: getSetupGuideUrl(),
        };
    }

    private async pushState() {
        this.post({ type: 'state', state: await this.collectState() });
    }

    // ----------------------------------------------------------------- router

    private async onMessage(msg: { cmd: string; [k: string]: unknown }) {
        switch (msg.cmd) {
            case 'ready':
                await this.pushState();
                return;
            case 'detectPython':
                await this.runTask('detectPython', async () => {
                    this.freshPathEnv = undefined;
                    const p = await detectPythonAll();
                    return p.best
                        ? 'Python ' + p.all.find(x => x.cmd === p.best)?.version + ' found (' + p.best + ').'
                        : 'No Python 3.11+ found on this machine.';
                });
                return;
            case 'installPython':
                await this.runTask('installPython', (onLine) => this.taskInstallPython(onLine));
                return;
            case 'pickInstallDir': {
                const picked = await vscode.window.showOpenDialog({
                    canSelectFiles: false,
                    canSelectFolders: true,
                    canSelectMany: false,
                    openLabel: 'Install Orion here',
                    defaultUri: vscode.Uri.file(this.installDir),
                });
                if (picked && picked[0]) { this.installDir = picked[0].fsPath; }
                await this.pushState();
                return;
            }
            case 'setInstallDir': {
                const dir = String(msg.dir || '').trim();
                if (dir) { this.installDir = dir; }
                return;
            }
            case 'downloadBackend':
                await this.runTask('downloadBackend', (onLine) => this.taskDownload(msg, onLine));
                return;
            case 'installDeps':
                await this.runTask('installDeps', (onLine) => this.taskInstallDeps(onLine));
                return;
            case 'saveEnv':
                await this.runTask('saveEnv', () => this.taskSaveEnv(msg));
                return;
            case 'restartBackend':
                await this.runTask('restartBackend', async () => {
                    await restartBackend();
                    const env = await detectEnvironment();
                    return env.backendRunning
                        ? 'Backend restarted and answering on port 8000.'
                        : 'Backend restarted but port 8000 did not answer yet — give it a few seconds and press Re-check.';
                });
                return;
            case 'startBackend':
                await this.runTask('restartBackend', async () => {
                    await restartBackend();
                    return 'Start attempted — see step 5 status.';
                });
                return;
            case 'openChat':
                await vscode.commands.executeCommand('orion.openChat');
                return;
            case 'openGuide': {
                const url = getSetupGuideUrl();
                if (url) { await vscode.env.openExternal(vscode.Uri.parse(url)); }
                return;
            }
            case 'openExternal': {
                const url = String(msg.url || '');
                if (this.isAllowedExternal(url)) {
                    await vscode.env.openExternal(vscode.Uri.parse(url));
                }
                return;
            }
            default:
                return;
        }
    }

    private isAllowedExternal(url: string): boolean {
        try {
            const u = new URL(url);
            if (u.protocol !== 'https:') { return false; }
            const hosts: string[] = ['aistudio.google.com', 'www.python.org'];
            const repoBase = getRepoBaseUrl();
            if (repoBase) {
                try { hosts.push(new URL(repoBase).host); } catch { /* ignore bad metadata */ }
            }
            return hosts.includes(u.host);
        } catch {
            return false;
        }
    }

    /** Serialize long tasks: one at a time, with start/log/done messaging. */
    private async runTask(task: string, fn: (onLine: (l: string) => void) => Promise<string>) {
        if (this.busy) {
            this.post({ type: 'info', message: 'Another step is still running — please wait for it to finish.' });
            return;
        }
        this.busy = true;
        this.post({ type: 'taskStart', task });
        const onLine = (line: string) => this.post({ type: 'log', task, line });
        try {
            const message = await fn(onLine);
            this.post({ type: 'taskDone', task, ok: true, message });
        } catch (e: unknown) {
            const message = e instanceof Error ? e.message : String(e);
            this.post({ type: 'taskDone', task, ok: false, message });
        } finally {
            this.busy = false;
            await this.pushState();
        }
    }

    // ------------------------------------------------------------------ tasks

    private async taskInstallPython(onLine: (l: string) => void): Promise<string> {
        if (process.platform !== 'win32') {
            throw new Error('Automatic Python installation is Windows-only (winget). On macOS/Linux install Python 3.11+ from python.org, then press Re-check.');
        }
        const existing = await detectPythonAll(this.freshPathEnv);
        if (existing.best) {
            return 'Python ' + existing.all.find(x => x.cmd === existing.best)?.version + ' is already installed (' + existing.best + ') — nothing to do.';
        }
        onLine('Installing Python 3.12 via winget (this can take a few minutes)…');
        const r = await runProcess('winget', [
            'install', '-e', '--id', 'Python.Python.3.12',
            '--accept-package-agreements', '--accept-source-agreements', '--silent',
        ], { timeoutMs: 20 * 60 * 1000, env: this.freshPathEnv }, onLine);
        if (r.code !== 0) {
            throw new Error('winget exited with code ' + r.code + '. Install Python manually from python.org (tick "Add python.exe to PATH"), then press Re-check.');
        }
        await this.refreshPathEnv();
        const after = await detectPythonAll(this.freshPathEnv);
        if (!after.best) {
            throw new Error('winget finished but Python is still not visible. Close and reopen VS Code once (PATH refresh), then press Re-check.');
        }
        return 'Python ' + after.all.find(x => x.cmd === after.best)?.version + ' installed (' + after.best + ').';
    }

    private async refreshPathEnv(): Promise<void> {
        if (process.platform !== 'win32') { return; }
        const r = await runProcess('powershell', [
            '-NoProfile', '-Command',
            "[Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')",
        ], { timeoutMs: 15000 });
        const lines = r.out.trim().split(/\r?\n/);
        const p = (lines[lines.length - 1] || '').trim();
        if (p) {
            this.freshPathEnv = { ...process.env, Path: p, PATH: p };
        }
    }

    private async taskDownload(msg: { [k: string]: unknown }, onLine: (l: string) => void): Promise<string> {
        const dir = String(msg.dir || this.installDir || '').trim() || this.installDir;
        this.installDir = dir;
        const zipUrl = getRepoZipUrl();
        if (!zipUrl) {
            throw new Error('Cannot derive the download URL — the extension repository metadata is missing.');
        }
        onLine('Source: ' + zipUrl);
        const zipPath = path.join(os.tmpdir(), 'orion-wizard-' + Date.now() + '.zip');
        await downloadFile(zipUrl, zipPath, onLine);
        onLine('Downloaded ' + (fs.statSync(zipPath).size / 1048576).toFixed(1) + ' MB — extracting…');

        const extractRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'orion-extract-'));
        try {
            // bsdtar ships with Windows 10 1803+, macOS and every Linux —
            // it extracts .zip without any extra tooling.
            let r = await runProcess('tar', ['-xf', zipPath, '-C', extractRoot], { timeoutMs: 5 * 60 * 1000 }, onLine);
            if (r.code !== 0 && process.platform === 'win32') {
                onLine('tar failed — retrying with PowerShell Expand-Archive…');
                r = await runProcess('powershell', [
                    '-NoProfile', '-Command',
                    "Expand-Archive -LiteralPath '" + zipPath + "' -DestinationPath '" + extractRoot + "' -Force",
                ], { timeoutMs: 5 * 60 * 1000 }, onLine);
            }
            if (r.code !== 0) {
                throw new Error('Could not extract the download (exit code ' + r.code + ').');
            }
            const inner = fs.readdirSync(extractRoot, { withFileTypes: true }).find(d => d.isDirectory());
            if (!inner) {
                throw new Error('The download did not contain a repository folder.');
            }
            const src = path.join(extractRoot, inner.name);

            // Preserve the user's existing .env (holds their API keys).
            const backendDir = path.join(dir, 'backend');
            const envFile = path.join(backendDir, '.env');
            let savedEnv: string | null = null;
            if (fs.existsSync(envFile)) {
                savedEnv = fs.readFileSync(envFile, 'utf-8');
                onLine('Existing .env found — your API keys will be preserved.');
            }

            fs.mkdirSync(dir, { recursive: true });
            fs.cpSync(src, dir, { recursive: true });
            if (savedEnv !== null) {
                fs.mkdirSync(backendDir, { recursive: true });
                fs.writeFileSync(envFile, savedEnv, 'utf-8');
                onLine('.env restored.');
            }
            onLine('Orion files installed to: ' + dir);

            // Point the extension at the fresh backend so auto-start and the
            // remaining wizard steps find it from ANY workspace.
            await vscode.workspace.getConfiguration('orion')
                .update('backendPath', backendDir, vscode.ConfigurationTarget.Global);
            onLine('Setting orion.backendPath -> ' + backendDir);
            return 'Backend downloaded to ' + dir + ' and orion.backendPath updated.';
        } finally {
            fs.rmSync(extractRoot, { recursive: true, force: true });
            fs.rmSync(zipPath, { force: true });
        }
    }

    private async resolveBackendDir(): Promise<string> {
        const env = await detectEnvironment();
        if (env.backendPath) { return env.backendPath; }
        const cand = path.join(this.installDir, 'backend');
        if (fs.existsSync(path.join(cand, 'app', 'main.py'))) { return cand; }
        return '';
    }

    private async taskInstallDeps(onLine: (l: string) => void): Promise<string> {
        const backendDir = await this.resolveBackendDir();
        if (!backendDir) {
            throw new Error('No backend folder found — run step 2 (Download) first.');
        }
        if (!fs.existsSync(path.join(backendDir, 'requirements.txt'))) {
            throw new Error('requirements.txt is missing in ' + backendDir + ' — the download may be incomplete; run step 2 again.');
        }
        let venvPython = findVenvPython(backendDir);
        if (venvPython) {
            onLine('Reusing existing virtual environment: ' + venvPython);
        } else {
            const py = await detectPythonAll(this.freshPathEnv);
            if (!py.best) {
                throw new Error('No suitable Python (3.11+) found — complete step 1 first.');
            }
            const parts = py.best.split(' ');
            const cmd = parts[0];
            const args = parts.slice(1);
            onLine('Creating virtual environment (.venv) with ' + py.best + '…');
            const rv = await runProcess(cmd, [...args, '-m', 'venv', '.venv'], {
                cwd: backendDir, timeoutMs: 3 * 60 * 1000, env: this.freshPathEnv,
            }, onLine);
            if (rv.code !== 0) {
                throw new Error('Creating the virtual environment failed (exit code ' + rv.code + ').');
            }
            venvPython = findVenvPython(backendDir);
            if (!venvPython) {
                throw new Error('The virtual environment was created but its python executable was not found.');
            }
        }
        onLine('Upgrading pip…');
        await runProcess(venvPython, ['-m', 'pip', 'install', '--upgrade', 'pip', '--quiet', '--disable-pip-version-check'], {
            cwd: backendDir, timeoutMs: 5 * 60 * 1000, env: this.freshPathEnv,
        }, onLine);
        onLine('Installing dependencies (2–5 minutes, one time)…');
        const ri = await runProcess(venvPython, ['-m', 'pip', 'install', '-r', 'requirements.txt', '--progress-bar', 'off', '--disable-pip-version-check'], {
            cwd: backendDir, timeoutMs: 30 * 60 * 1000, env: this.freshPathEnv,
        }, onLine);
        if (ri.code !== 0) {
            throw new Error('pip install failed (exit code ' + ri.code + ') — check your internet connection and the log above.');
        }
        onLine('Smoke test: importing the backend…');
        const rs = await runProcess(venvPython, ['-c', 'import app.main'], {
            cwd: backendDir, timeoutMs: 5 * 60 * 1000, env: this.freshPathEnv,
        }, onLine);
        if (rs.code !== 0) {
            throw new Error('The backend import smoke test failed (exit code ' + rs.code + ') — see the log above.');
        }
        return 'Dependencies installed and smoke test passed in ' + backendDir + '.';
    }

    private async taskSaveEnv(msg: { [k: string]: unknown }): Promise<string> {
        const backendDir = await this.resolveBackendDir();
        if (!backendDir) {
            throw new Error('No backend folder found — run step 2 (Download) first.');
        }
        const envPath = path.join(backendDir, '.env');
        let baseText = '';
        if (fs.existsSync(envPath)) {
            baseText = fs.readFileSync(envPath, 'utf-8');
        } else {
            const example = path.join(backendDir, '.env.example');
            if (fs.existsSync(example)) {
                baseText = fs.readFileSync(example, 'utf-8');
            }
        }
        const existing = parseEnvText(baseText);

        // Merge Gemini keys: a non-empty field overrides, an empty field KEEPS
        // the stored key (the form only ever shows masked values).
        const payloadKeys = Array.isArray(msg.geminiKeys) ? msg.geminiKeys as Array<{ index: number; value: string }> : [];
        const maxIdx = Math.max(0, ...[...existing.gemini.keys()], ...payloadKeys.map(k => k.index || 0));
        const geminiFinal: string[] = [];
        for (let i = 1; i <= maxIdx; i++) {
            const upd = payloadKeys.find(k => k.index === i);
            const v = (upd && String(upd.value || '').trim()) ? String(upd.value).trim() : (existing.gemini.get(i) || '');
            if (v) { geminiFinal.push(v); }
        }

        // Merge provider groups the same way; explicit removals are honoured.
        const removedNs = new Set((Array.isArray(msg.removedProviderNs) ? msg.removedProviderNs as number[] : []).map(n => Number(n)));
        const provFinal = new Map<number, RawProvider>();
        for (const [n, g] of existing.providers) {
            if (!removedNs.has(n)) { provFinal.set(n, { ...g }); }
        }
        const payloadProviders = Array.isArray(msg.providers)
            ? msg.providers as Array<{ n: number; name?: string; apiKey?: string; baseUrl?: string; models?: string }>
            : [];
        for (const p of payloadProviders) {
            const n = Number(p.n);
            if (!n || removedNs.has(n)) { continue; }
            const cur = provFinal.get(n) || { name: '', apiKey: '', baseUrl: '', models: '', temperature: '' };
            provFinal.set(n, {
                name: String(p.name || '').trim() || cur.name,
                apiKey: String(p.apiKey || '').trim() || cur.apiKey,
                baseUrl: p.baseUrl !== undefined && p.baseUrl !== null ? String(p.baseUrl).trim() : cur.baseUrl,
                models: String(p.models || '').trim() || cur.models,
                temperature: cur.temperature,
            });
        }
        const groups = [...provFinal.entries()]
            .sort((a, b) => a[0] - b[0])
            .map(([, g]) => g)
            .filter(g => g.apiKey && g.models);

        const defaultModel = String(msg.defaultModel || '').trim() || existing.defaultModel || 'gemini/gemini-3.5-flash-lite';

        // Keep every non-managed line (all comments, custom vars) untouched;
        // the managed block is rewritten at the end of the file.
        const kept = baseText.split(/\r?\n/).filter(raw => {
            const t = raw.trim();
            if (!t || t.startsWith('#')) { return true; }
            const eq = t.indexOf('=');
            if (eq < 0) { return true; }
            const key = t.slice(0, eq).trim();
            return !/^GEMINI_API_KEY_\d+$/.test(key)
                && key !== 'DEFAULT_MODEL'
                && !/^PROVIDER_\d+_(NAME|API_KEY|BASE_URL|MODELS|TEMPERATURE)$/.test(key);
        });
        while (kept.length && !kept[kept.length - 1].trim()) { kept.pop(); }

        const block: string[] = [
            '',
            '# --- Managed by the Orion Setup Wizard (saved ' + new Date().toISOString().slice(0, 10) + ') ---',
        ];
        if (geminiFinal.length) {
            geminiFinal.forEach((v, i) => block.push('GEMINI_API_KEY_' + (i + 1) + '=' + v));
        } else {
            block.push('# GEMINI_API_KEY_1=   (no Gemini key saved yet)');
        }
        block.push('DEFAULT_MODEL=' + defaultModel);
        groups.forEach((g, i) => {
            const n = i + 1;
            if (g.name) { block.push('PROVIDER_' + n + '_NAME=' + g.name); }
            block.push('PROVIDER_' + n + '_API_KEY=' + g.apiKey);
            if (g.baseUrl) { block.push('PROVIDER_' + n + '_BASE_URL=' + g.baseUrl); }
            block.push('PROVIDER_' + n + '_MODELS=' + g.models);
            if (g.temperature) { block.push('PROVIDER_' + n + '_TEMPERATURE=' + g.temperature); }
        });

        fs.writeFileSync(envPath, kept.join('\n') + block.join('\n') + '\n', 'utf-8');
        return 'Saved ' + geminiFinal.length + ' Gemini key(s) and ' + groups.length + ' provider group(s) to ' + envPath
            + ' — restart the backend (step 5) to apply.';
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
        // NOTE: the inline page script deliberately avoids backtick template
        // literals so this TS template string stays the only interpolation
        // layer (no escaping bugs).
        return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="${csp}">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Orion Setup Wizard</title>
<style>
    :root { color-scheme: light dark; }
    body { font-family: var(--vscode-font-family); color: var(--vscode-foreground);
           background: var(--vscode-editor-background); padding: 16px 22px; margin: 0; }
    h1 { font-size: 1.35em; margin: 0 0 2px 0; }
    .sub { color: var(--vscode-descriptionForeground); margin: 0 0 18px 0; font-size: .92em; }
    .card { border: 1px solid var(--vscode-panel-border, #444); border-radius: 6px;
            margin-bottom: 14px; background: var(--vscode-sideBar-background, transparent); }
    .head { display: flex; align-items: center; gap: 10px; padding: 10px 14px; cursor: default; }
    .num { width: 24px; height: 24px; border-radius: 50%; display: flex; align-items: center;
           justify-content: center; font-weight: 600; font-size: .85em; flex: 0 0 auto;
           background: var(--vscode-badge-background); color: var(--vscode-badge-foreground); }
    .head h2 { font-size: 1.02em; margin: 0; flex: 1 1 auto; }
    .badge { font-size: .8em; padding: 2px 10px; border-radius: 10px; white-space: nowrap;
             background: var(--vscode-input-background); color: var(--vscode-descriptionForeground);
             border: 1px solid var(--vscode-panel-border, #555); max-width: 46%;
             overflow: hidden; text-overflow: ellipsis; }
    .badge.ok { background: var(--vscode-testing-iconPassed, #2ea043); color: #fff; border-color: transparent; }
    .badge.warn { background: var(--vscode-editorWarning-foreground, #b8860b); color: #fff; border-color: transparent; }
    .badge.run { background: var(--vscode-progressBar-background, #0e639c); color: #fff; border-color: transparent; }
    .badge.err { background: var(--vscode-errorForeground, #c72e2e); color: #fff; border-color: transparent; }
    .body { padding: 4px 14px 14px 14px; border-top: 1px solid var(--vscode-panel-border, #33333355); }
    .desc { color: var(--vscode-descriptionForeground); font-size: .9em; margin: 10px 0; }
    button { background: var(--vscode-button-background); color: var(--vscode-button-foreground);
             border: none; padding: 6px 14px; border-radius: 3px; cursor: pointer;
             font-size: .9em; margin: 2px 6px 2px 0; }
    button:hover { background: var(--vscode-button-hoverBackground); }
    button.secondary { background: var(--vscode-button-secondaryBackground, #3a3d41);
                       color: var(--vscode-button-secondaryForeground, #fff); }
    button:disabled { opacity: .5; cursor: default; }
    input[type=text], input[type=password] { background: var(--vscode-input-background);
        color: var(--vscode-input-foreground); border: 1px solid var(--vscode-input-border, #555);
        border-radius: 3px; padding: 5px 8px; font-size: .9em; margin: 2px 0; }
    input:focus { outline: 1px solid var(--vscode-focusBorder); }
    .row { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; margin: 4px 0; }
    .row input { flex: 1 1 160px; min-width: 120px; }
    pre.log { display: none; background: var(--vscode-textCodeBlock-background, #111);
        color: var(--vscode-textPreformat-foreground, #ccc); padding: 8px 10px; border-radius: 4px;
        font-size: .8em; max-height: 190px; overflow-y: auto; white-space: pre-wrap;
        word-break: break-all; margin: 8px 0 0 0; }
    pre.log.show { display: block; }
    .info { font-size: .88em; margin: 8px 0; padding: 7px 10px; border-radius: 4px; display: none;
        background: var(--vscode-inputValidation-infoBackground, #063b4f);
        border: 1px solid var(--vscode-inputValidation-infoBorder, #007acc); }
    .info.show { display: block; }
    .info.err { background: var(--vscode-inputValidation-errorBackground, #5a1d1d);
        border-color: var(--vscode-inputValidation-errorBorder, #be1100); }
    a { color: var(--vscode-textLink-foreground); cursor: pointer; }
    details { margin: 10px 0; }
    summary { cursor: pointer; color: var(--vscode-textLink-foreground); font-size: .9em; }
    .keyrow { display: flex; gap: 6px; align-items: center; margin: 4px 0; }
    .keyrow .idx { width: 22px; text-align: right; color: var(--vscode-descriptionForeground);
                   font-size: .85em; flex: 0 0 auto; }
    .keyrow input { flex: 1 1 auto; }
    .provgroup { border: 1px dashed var(--vscode-panel-border, #555); border-radius: 4px;
                 padding: 8px 10px; margin: 8px 0; }
    .muted { color: var(--vscode-descriptionForeground); font-size: .82em; }
    .toplinks { float: right; font-size: .85em; }
</style>
</head>
<body>
<div class="toplinks"><a id="guideLink">Full setup guide</a></div>
<h1>Orion Setup Wizard</h1>
<p class="sub">Install and configure Orion's local backend in five steps — no terminal needed. Your code and API keys never leave this computer.</p>

<div class="info" id="infoBox"></div>

<!-- STEP 1 -->
<div class="card" id="card1">
    <div class="head"><span class="num">1</span><h2>Python 3.11+</h2><span class="badge" id="badge1">checking…</span></div>
    <div class="body">
        <p class="desc">Orion's backend is a small Python server. The wizard first looks for Python 3.11 or newer on your system.</p>
        <button id="btnRecheckPy">Re-check</button>
        <button id="btnInstallPy" style="display:none">Install Python automatically (winget)</button>
        <span id="pyNote" class="muted"></span>
        <pre class="log" id="log1"></pre>
    </div>
</div>

<!-- STEP 2 -->
<div class="card" id="card2">
    <div class="head"><span class="num">2</span><h2>Download the backend</h2><span class="badge" id="badge2">checking…</span></div>
    <div class="body">
        <p class="desc">Downloads the latest Orion from GitHub (no git needed). An existing <b>.env with your API keys is always preserved</b>, and the setting <code>orion.backendPath</code> is updated automatically.</p>
        <div class="row">
            <span class="muted">Install folder:</span>
            <input type="text" id="installDir" style="flex:2 1 260px">
            <button class="secondary" id="btnBrowse">Browse…</button>
        </div>
        <button id="btnDownload">Download &amp; install</button>
        <pre class="log" id="log2"></pre>
    </div>
</div>

<!-- STEP 3 -->
<div class="card" id="card3">
    <div class="head"><span class="num">3</span><h2>Install dependencies</h2><span class="badge" id="badge3">checking…</span></div>
    <div class="body">
        <p class="desc">Creates the virtual environment (<code>.venv</code>) and installs the Python packages — about 2–5 minutes, one time only.</p>
        <button id="btnInstallDeps">Install dependencies</button>
        <pre class="log" id="log3"></pre>
    </div>
</div>

<!-- STEP 4 -->
<div class="card" id="card4">
    <div class="head"><span class="num">4</span><h2>API keys</h2><span class="badge" id="badge4">checking…</span></div>
    <div class="body">
        <p class="desc">Paste one or more <b>free</b> Gemini keys — Orion rotates them automatically when a quota runs out.
        Get keys at <a id="keyLink">aistudio.google.com/apikey</a>.
        Stored keys are shown <b>masked</b>; leave a field empty to keep the stored key, type a new value to replace it.</p>
        <div id="geminiKeys"></div>
        <button class="secondary" id="btnAddKey">+ Add key</button>
        <div class="row" style="margin-top:10px">
            <span class="muted">Primary model:</span>
            <input type="text" id="defaultModel" placeholder="gemini/gemini-3.5-flash-lite" style="flex:1 1 240px">
        </div>
        <details>
            <summary>Advanced: other providers (OpenRouter / OpenAI / Anthropic / DeepSeek / Qwen / Groq…)</summary>
            <p class="desc">Optional. Each group = one provider account. Groups are tried in order after the Gemini keys. A model name without <code>/</code> gets the <code>openai/</code> prefix (correct for every OpenAI-compatible endpoint); names like <code>anthropic/claude-…</code> go through as-is.</p>
            <div id="providerGroups"></div>
            <button class="secondary" id="btnAddProv">+ Add provider group</button>
        </details>
        <button id="btnSaveEnv" style="margin-top:10px">Save keys to backend/.env</button>
        <pre class="log" id="log4"></pre>
    </div>
</div>

<!-- STEP 5 -->
<div class="card" id="card5">
    <div class="head"><span class="num">5</span><h2>Start &amp; chat</h2><span class="badge" id="badge5">checking…</span></div>
    <div class="body">
        <p class="desc">Restart the backend so it picks up the new keys, then open the chat and send your first task. From now on the backend also starts automatically whenever VS Code opens.</p>
        <button id="btnRestart">Restart backend</button>
        <button id="btnOpenChat">Open chat</button>
        <pre class="log" id="log5"></pre>
    </div>
</div>

<p class="muted">Logs after setup: <code>&lt;project&gt;\\.orion\\logs\\backend.log</code> (structured) and <code>&lt;project&gt;\\.orion\\logs\\orion_stdout.log</code> (startup + routing lines).</p>

<script nonce="${nonce}">
(function () {
    var vscode = acquireVsCodeApi();
    var state = null;
    var runningTask = null;
    var formDirty = false;
    var envSignature = '';
    var removedProviderNs = [];
    var TASK_STEP = { detectPython: 1, installPython: 1, downloadBackend: 2, installDeps: 3, saveEnv: 4, restartBackend: 5 };

    function el(id) { return document.getElementById(id); }
    function post(m) { vscode.postMessage(m); }

    function showInfo(message, isErr) {
        var box = el('infoBox');
        box.textContent = message;
        box.className = 'show info' + (isErr ? ' err' : '');
    }
    function hideInfo() { el('infoBox').className = 'info'; }

    function setBadge(step, cls, text) {
        var b = el('badge' + step);
        b.className = 'badge' + (cls ? ' ' + cls : '');
        b.textContent = text;
        b.title = text;
    }
    function appendLog(step, line) {
        var pre = el('log' + step);
        pre.className = 'log show';
        pre.textContent += line + '\\n';
        pre.scrollTop = pre.scrollHeight;
    }
    function clearLog(step) { el('log' + step).textContent = ''; }
    function setButtonsEnabled(enabled) {
        var btns = document.querySelectorAll('button');
        for (var i = 0; i < btns.length; i++) { btns[i].disabled = !enabled; }
    }

    // ---- step 4 form rendering -----------------------------------------
    function geminiRow(index, masked) {
        var div = document.createElement('div');
        div.className = 'keyrow';
        var idx = document.createElement('span');
        idx.className = 'idx';
        idx.textContent = '#' + index;
        var inp = document.createElement('input');
        inp.type = 'password';
        inp.placeholder = masked ? ('saved: ' + masked + '  (leave empty to keep)') : 'paste Gemini API key…';
        inp.dataset.idx = String(index);
        inp.addEventListener('input', function () { formDirty = true; });
        div.appendChild(idx);
        div.appendChild(inp);
        return div;
    }

    function providerGroup(p) {
        var wrap = document.createElement('div');
        wrap.className = 'provgroup';
        wrap.dataset.n = String(p.n);
        var title = document.createElement('div');
        title.className = 'muted';
        title.textContent = 'Group ' + p.n + (p.name ? (' — ' + p.name) : '') + (p.maskedKey ? ('  (key saved: ' + p.maskedKey + ')') : '');
        var r1 = document.createElement('div'); r1.className = 'row';
        var name = document.createElement('input'); name.type = 'text'; name.placeholder = 'Name (e.g. DeepSeek)'; name.value = p.name || ''; name.dataset.f = 'name';
        var key = document.createElement('input'); key.type = 'password'; key.placeholder = p.maskedKey ? ('saved: ' + p.maskedKey + ' (empty = keep)') : 'API key'; key.dataset.f = 'apiKey';
        r1.appendChild(name); r1.appendChild(key);
        var r2 = document.createElement('div'); r2.className = 'row';
        var base = document.createElement('input'); base.type = 'text'; base.placeholder = 'Base URL (optional — OpenAI-compatible endpoints)'; base.value = p.baseUrl || ''; base.dataset.f = 'baseUrl';
        var models = document.createElement('input'); models.type = 'text'; models.placeholder = 'Models, comma-separated (required)'; models.value = p.models || ''; models.dataset.f = 'models';
        r2.appendChild(base); r2.appendChild(models);
        var del = document.createElement('button');
        del.className = 'secondary';
        del.textContent = 'Remove group';
        del.addEventListener('click', function () {
            removedProviderNs.push(p.n);
            wrap.remove();
            formDirty = true;
        });
        wrap.appendChild(title); wrap.appendChild(r1); wrap.appendChild(r2); wrap.appendChild(del);
        wrap.querySelectorAll('input').forEach(function (i) { i.addEventListener('input', function () { formDirty = true; }); });
        return wrap;
    }

    function nextProviderN() {
        var max = 0;
        document.querySelectorAll('#providerGroups .provgroup').forEach(function (g) {
            var n = parseInt(g.dataset.n, 10);
            if (n > max) { max = n; }
        });
        return max + 1;
    }

    function renderEnvForm(envFile) {
        var container = el('geminiKeys');
        container.innerHTML = '';
        var keys = (envFile && envFile.exists) ? envFile.geminiKeys : [];
        var count = Math.max(keys.length, 1);
        for (var i = 1; i <= count; i++) {
            var saved = keys.find(function (k) { return k.index === i; });
            container.appendChild(geminiRow(i, saved ? saved.masked : ''));
        }
        var pg = el('providerGroups');
        pg.innerHTML = '';
        removedProviderNs = [];
        if (envFile && envFile.exists) {
            envFile.providers.forEach(function (p) { pg.appendChild(providerGroup(p)); });
        }
        var dm = el('defaultModel');
        dm.value = (envFile && envFile.defaultModel) ? envFile.defaultModel : '';
        dm.placeholder = 'gemini/gemini-3.5-flash-lite';
        formDirty = false;
    }

    function envSig(envFile) {
        if (!envFile || !envFile.exists) { return 'none'; }
        return envFile.path + '|' + envFile.geminiKeys.map(function (k) { return k.index + ':' + k.masked; }).join(',')
            + '|' + envFile.defaultModel
            + '|' + envFile.providers.map(function (p) { return p.n + ':' + p.maskedKey + ':' + p.models; }).join(',');
    }

    function collectEnvPayload() {
        var geminiKeys = [];
        document.querySelectorAll('#geminiKeys input').forEach(function (inp) {
            geminiKeys.push({ index: parseInt(inp.dataset.idx, 10), value: inp.value });
        });
        var providers = [];
        document.querySelectorAll('#providerGroups .provgroup').forEach(function (g) {
            var item = { n: parseInt(g.dataset.n, 10) };
            g.querySelectorAll('input').forEach(function (inp) { item[inp.dataset.f] = inp.value; });
            providers.push(item);
        });
        return { cmd: 'saveEnv', geminiKeys: geminiKeys, providers: providers,
                 removedProviderNs: removedProviderNs, defaultModel: el('defaultModel').value };
    }

    // ---- state rendering ------------------------------------------------
    function render() {
        if (!state) { return; }
        // step 1
        var best = state.python.best;
        var all = state.python.all || [];
        if (best) {
            var found = all.find(function (p) { return p.cmd === best; });
            setBadge(1, 'ok', 'Python ' + (found ? found.version : '') + ' (' + best + ')');
            el('btnInstallPy').style.display = 'none';
            el('pyNote').textContent = '';
        } else {
            var old = all.filter(function (p) { return !p.ok; });
            setBadge(1, 'err', 'not found' + (old.length ? (' (' + old.map(function (p) { return p.cmd + ' = ' + p.version; }).join(', ') + ' too old)') : ''));
            el('btnInstallPy').style.display = (state.platform === 'win32' && state.winget) ? '' : 'none';
            el('pyNote').textContent = (state.platform === 'win32' && !state.winget)
                ? 'winget not available — install Python from python.org and tick "Add python.exe to PATH".'
                : (state.platform === 'win32' ? '' : 'Install Python 3.11+ from python.org, then press Re-check.');
        }
        // step 2
        el('installDir').value = state.installDir || '';
        var b = state.backend;
        if (b.backendPath) {
            setBadge(2, 'ok', b.backendPath + ' (' + b.backendSource + ')');
            el('btnDownload').textContent = 'Re-download (update code, keep .env)';
        } else {
            setBadge(2, 'err', 'no backend folder found');
            el('btnDownload').textContent = 'Download & install';
        }
        // step 3
        if (b.backendPath && b.venvPython) {
            setBadge(3, 'ok', 'venv ready');
        } else if (b.backendPath) {
            setBadge(3, 'warn', 'backend present, venv missing');
        } else {
            setBadge(3, '', 'waiting for step 2');
        }
        // step 4
        var ef = state.envFile;
        if (ef && ef.exists && ef.geminiKeys.length > 0) {
            setBadge(4, 'ok', ef.geminiKeys.length + ' Gemini key(s)' + (ef.providers.length ? (', ' + ef.providers.length + ' group(s)') : ''));
        } else if (ef && ef.exists) {
            setBadge(4, 'warn', '.env exists, no Gemini key');
        } else if (b.backendPath) {
            setBadge(4, 'warn', 'no .env yet');
        } else {
            setBadge(4, '', 'waiting for step 2');
        }
        var sig = envSig(ef);
        if (sig !== envSignature) {
            envSignature = sig;
            if (!formDirty) { renderEnvForm(ef); }
        }
        // step 5
        if (b.backendRunning) {
            setBadge(5, 'ok', 'backend running on port 8000');
        } else if (b.backendPath && b.venvPython) {
            setBadge(5, 'warn', 'ready to start');
        } else {
            setBadge(5, '', 'waiting for steps 2–3');
        }
    }

    // ---- messages from the extension ------------------------------------
    window.addEventListener('message', function (event) {
        var msg = event.data;
        if (msg.type === 'state') {
            state = msg.state;
            render();
            if (runningTask) { return; }
            setButtonsEnabled(true);
        } else if (msg.type === 'taskStart') {
            runningTask = msg.task;
            var step = TASK_STEP[msg.task];
            if (step) { clearLog(step); setBadge(step, 'run', 'running…'); }
            setButtonsEnabled(false);
            hideInfo();
        } else if (msg.type === 'log') {
            var s = TASK_STEP[msg.task];
            if (s) { appendLog(s, msg.line); }
        } else if (msg.type === 'taskDone') {
            var st = TASK_STEP[msg.task];
            runningTask = null;
            setButtonsEnabled(true);
            if (msg.task === 'saveEnv' && msg.ok) { formDirty = false; envSignature = ''; }
            if (!msg.ok && st) { setBadge(st, 'err', 'failed'); }
            showInfo((msg.ok ? '\\u2705 ' : '\\u274c ') + msg.message, !msg.ok);
        } else if (msg.type === 'info') {
            showInfo(msg.message, false);
        }
    });

    // ---- buttons ---------------------------------------------------------
    el('btnRecheckPy').addEventListener('click', function () { post({ cmd: 'detectPython' }); });
    el('btnInstallPy').addEventListener('click', function () { post({ cmd: 'installPython' }); });
    el('btnBrowse').addEventListener('click', function () { post({ cmd: 'pickInstallDir' }); });
    el('installDir').addEventListener('change', function () { post({ cmd: 'setInstallDir', dir: el('installDir').value }); });
    el('btnDownload').addEventListener('click', function () { post({ cmd: 'downloadBackend', dir: el('installDir').value }); });
    el('btnInstallDeps').addEventListener('click', function () { post({ cmd: 'installDeps' }); });
    el('btnAddKey').addEventListener('click', function () {
        var inputs = document.querySelectorAll('#geminiKeys input');
        var max = 0;
        inputs.forEach(function (i) { var n = parseInt(i.dataset.idx, 10); if (n > max) { max = n; } });
        el('geminiKeys').appendChild(geminiRow(max + 1, ''));
        formDirty = true;
    });
    el('btnAddProv').addEventListener('click', function () {
        el('providerGroups').appendChild(providerGroup({ n: nextProviderN(), name: '', maskedKey: '', baseUrl: '', models: '' }));
        formDirty = true;
    });
    el('btnSaveEnv').addEventListener('click', function () { post(collectEnvPayload()); });
    el('btnRestart').addEventListener('click', function () { post({ cmd: 'restartBackend' }); });
    el('btnOpenChat').addEventListener('click', function () { post({ cmd: 'openChat' }); });
    el('guideLink').addEventListener('click', function () { post({ cmd: 'openGuide' }); });
    el('keyLink').addEventListener('click', function () { post({ cmd: 'openExternal', url: 'https://aistudio.google.com/apikey' }); });

    post({ cmd: 'ready' });
}());
</script>
</body>
</html>`;
    }
}

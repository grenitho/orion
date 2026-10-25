import * as vscode from 'vscode';
import { exec, ChildProcess } from 'child_process';
import * as path from 'path';
import * as net from 'net';
import * as fs from 'fs';
import { orionHeaders } from './authToken';

let backendProcess: ChildProcess | null = null;

// >>> UPDATE-013: the relative-backendPath warning (scope c) fires at most
// once per extension-host session — findBackendFolderWithSource is called
// from several paths (auto-start, wizard refresh) and must not spam dialogs.
let warnedRelativeBackendPath = false;

// ---------------------------------------------------------------------------
// >>> UPDATE-013: port-8000 identity probes.
// "Something answers on port 8000" is NOT the same as "a FRESH Orion backend
// answers on port 8000". Before this change ensureBackendRunning() early-
// returned on a bare TCP connect, so a stale orphan (started before the last
// code update), a backend from a different installation, or a completely
// unrelated app were all silently accepted. The backend now exposes
// GET /health (token-guarded) + a PID file; the probes below fingerprint
// whatever is listening and classify it.
// ---------------------------------------------------------------------------

/** Body of GET /health — keep in sync with backend/app/runtime_id.py. */
export interface HealthInfo {
    app?: string;
    version?: string;
    pid?: number;
    started_at?: number;       // epoch seconds (Python time.time())
    backend_path?: string;     // absolute path of the backend folder
    project_path?: string | null;
}

export type Port8000Inspection =
    | { kind: 'free' }                                  // nothing listening
    | { kind: 'fresh'; health: HealthInfo }             // the right, up-to-date Orion
    | { kind: 'stale'; health: HealthInfo | null; reason: string; message: string }
    | { kind: 'legacy-orion'; message: string }         // Orion, but pre-013 (no /health)
    | { kind: 'token-mismatch'; message: string }       // Orion, but rejects our token
    | { kind: 'foreign' };                              // some other application

async function fetchWithTimeout(
    url: string,
    headers: Record<string, string>,
    timeoutMs = 2500
): Promise<{ status: number; body: unknown } | null> {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
        const res = await fetch(url, { headers, signal: ctrl.signal });
        let body: unknown = null;
        try { body = await res.json(); } catch { /* non-JSON answer */ }
        return { status: res.status, body };
    } catch {
        return null; // connection refused / timeout / abort
    } finally {
        clearTimeout(timer);
    }
}

async function probeHealth(): Promise<{ status: number; body: unknown } | null> {
    return fetchWithTimeout('http://127.0.0.1:8000/health', orionHeaders());
}

async function probeRoot(): Promise<{ status: number; body: unknown } | null> {
    return fetchWithTimeout('http://127.0.0.1:8000/', {});
}

function looksLikeOrionRoot(body: unknown): boolean {
    const b = body as { status?: unknown } | null;
    return !!b && typeof b.status === 'string' && b.status.includes('Orion Backend is running');
}

/** True when both paths point at the same folder (Windows is case-insensitive). */
function sameFolder(a: string, b: string): boolean {
    if (!a || !b) { return false; }
    const na = path.resolve(a);
    const nb = path.resolve(b);
    return process.platform === 'win32' ? na.toLowerCase() === nb.toLowerCase() : na === nb;
}

/** Newest mtime (epoch SECONDS) among the .py files under backend/app
 *  (recursive) plus requirements.txt.
 *  A backend that booted BEFORE this timestamp is running code that has since
 *  been modified — the classic "patch applied but the orphan still serves the
 *  old code" trap of the local-first workflow. Bounded scan (~50 small files);
 *  venv/__pycache__ are excluded by only walking app/ + one file. */
function newestBackendCodeMtime(backendPath: string): number {
    let newest = 0;
    const walk = (dir: string) => {
        let entries: fs.Dirent[];
        try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch { return; }
        for (const e of entries) {
            if (e.name === '__pycache__') { continue; }
            const full = path.join(dir, e.name);
            if (e.isDirectory()) {
                walk(full);
            } else if (e.name.endsWith('.py')) {
                try { newest = Math.max(newest, fs.statSync(full).mtimeMs / 1000); } catch { /* unreadable */ }
            }
        }
    };
    walk(path.join(backendPath, 'app'));
    try { newest = Math.max(newest, fs.statSync(path.join(backendPath, 'requirements.txt')).mtimeMs / 1000); } catch { /* optional */ }
    return newest;
}

/** Read <repo>/.orion/orion_backend.pid (written by the backend's lifespan). */
function readPidFile(backendPath: string): { pid?: number; started_at?: number; version?: string } | null {
    try {
        const raw = fs.readFileSync(path.resolve(backendPath, '..', '.orion', 'orion_backend.pid'), 'utf-8');
        const parsed = JSON.parse(raw);
        return (parsed && typeof parsed === 'object') ? parsed : null;
    } catch {
        return null;
    }
}

/** Classify whatever is listening on port 8000. */
export async function inspectPort8000(workspacePath: string): Promise<Port8000Inspection> {
    if (!(await checkPort(8000))) { return { kind: 'free' }; }

    const health = await probeHealth();
    const h = (health && health.status === 200 ? health.body : null) as HealthInfo | null;
    if (h && h.app === 'orion-backend') {
        const found = findBackendFolder(workspacePath);
        if (found) {
            if (!sameFolder(h.backend_path || '', found)) {
                return {
                    kind: 'stale', health: h, reason: 'different-folder',
                    message: 'Orion: port 8000 is answered by a backend from a DIFFERENT Orion installation, not the folder this workspace uses. Restart so the correct backend takes over?',
                };
            }
            const pidInfo = readPidFile(found);
            if (pidInfo && typeof pidInfo.pid === 'number' && typeof h.pid === 'number' && pidInfo.pid !== h.pid) {
                return {
                    kind: 'stale', health: h, reason: 'pid-mismatch',
                    message: `Orion: the process on port 8000 (PID ${h.pid}) does not match this installation's PID file (PID ${pidInfo.pid}) — it looks like an orphan left over from a previous session. Restart it now?`,
                };
            }
            const codeMtime = newestBackendCodeMtime(found);
            if (typeof h.started_at === 'number' && codeMtime > h.started_at + 2) {
                return {
                    kind: 'stale', health: h, reason: 'old-code',
                    message: 'Orion: the backend on port 8000 was started BEFORE the backend code was last modified — it is still running the OLD code (typical right after an upgrade). Restart it now to load the updated code?',
                };
            }
        }
        return { kind: 'fresh', health: h };
    }

    // Port is alive but /health did not answer like a healthy post-013 Orion.
    // The unauthenticated root probe tells us whether it is Orion at all.
    const root = await probeRoot();
    if (root && looksLikeOrionRoot(root.body)) {
        if (health && health.status === 401) {
            return {
                kind: 'token-mismatch',
                message: "Orion: the backend on port 8000 rejected Orion's shared access token (it may run under a different Windows user, or the token file changed since it started). Restart it now to re-sync?",
            };
        }
        return {
            kind: 'legacy-orion',
            message: 'Orion: an OLDER Orion backend (without the /health identity endpoint) is answering on port 8000. Restart it now to run the current version?',
        };
    }
    return { kind: 'foreign' };
}
// >>> END OF UPDATE-013 identity probes

// >>> UPDATE-010: the setup-guide URL is DERIVED from the extension's own
// package.json `repository` metadata at activation — never hardcoded here.
// Two reasons: the security-posture test forbids the maintainer identity in
// orion/src/**.ts, and a single source of truth keeps the link correct if
// the repository ever moves.
// >>> UPDATE-012: the same metadata also feeds the Setup Wizard's download
// URL (codeload zip of the default branch), so the wizard never hardcodes
// an owner/repo either.
let setupGuideUrl = '';
let repoBaseUrl = '';

export function initSetupGuide(context: vscode.ExtensionContext) {
    const repoUrl: string = context.extension?.packageJSON?.repository?.url || '';
    repoBaseUrl = repoUrl.replace(/\.git$/, '');
    setupGuideUrl = repoBaseUrl
        ? `${repoBaseUrl}/blob/main/docs/PANDUAN_SETUP.md`
        : '';
    console.log(`[BACKEND] Setup guide URL: ${setupGuideUrl || '(unavailable — repository metadata missing)'}`);
}

export function getSetupGuideUrl(): string {
    return setupGuideUrl;
}

export function getRepoBaseUrl(): string {
    return repoBaseUrl;
}

// https://github.com/<owner>/<repo> -> https://codeload.github.com/<owner>/<repo>/zip/refs/heads/main
export function getRepoZipUrl(): string {
    const m = repoBaseUrl.match(/^https?:\/\/github\.com\/([^/]+)\/([^/]+)/);
    return m ? `https://codeload.github.com/${m[1]}/${m[2]}/zip/refs/heads/main` : '';
}

function checkPort(port: number): Promise<boolean> {
    return new Promise((resolve) => {
        const tester = net.createConnection({ port, host: '127.0.0.1' }, () => {
            tester.destroy();
            resolve(true);
        });
        tester.on('error', () => {
            resolve(false);
        });
    });
}

// >>> UPDATE-010: beginner-proof backend discovery.
// 1. The new `orion.backendPath` setting (GUI) and the legacy
//    ORION_BACKEND_PATH env var take priority, so the backend may live
//    anywhere — no more editing this source file.
// 2. The strict validation stays: a candidate only counts when it contains
//    `app/main.py`, which prevents false positives on unrelated folders.
// >>> UPDATE-012: also reports WHERE the folder came from, so the Setup
// Wizard can explain the current state to the user.
export function findBackendFolderWithSource(workspacePath: string): { path: string; source: string } {
    const settingPath = (vscode.workspace.getConfiguration('orion').get<string>('backendPath') || '').trim();
    // >>> UPDATE-013 (scope c): a RELATIVE orion.backendPath is almost always a
    // mistake — it resolves against the extension host's working directory,
    // which nobody controls, so discovery "randomly" fails. Warn once per
    // session (the discovery below still tries the value, then falls through
    // to the normal candidates).
    if (settingPath && !path.isAbsolute(settingPath) && !warnedRelativeBackendPath) {
        warnedRelativeBackendPath = true;
        console.warn(`[BACKEND] ⚠️ The setting orion.backendPath is RELATIVE ("${settingPath}"). Relative paths resolve against an unpredictable working directory; set the FULL path to the backend folder instead.`);
        void vscode.window.showWarningMessage(
            `Orion: the setting "orion.backendPath" contains a relative path ("${settingPath}"). Relative paths are resolved against an unpredictable working directory and usually fail — set the FULL path to your backend folder (the one containing app/main.py).`,
            'Open Settings', 'Dismiss'
        ).then(pick => {
            if (pick === 'Open Settings') {
                void vscode.commands.executeCommand('workbench.action.openSettings', 'orion.backendPath');
            }
        });
    }
    const candidates: Array<[string, string]> = [
        [settingPath, 'setting'],
        [(process.env.ORION_BACKEND_PATH || '').trim(), 'env'],
        [path.join(workspacePath, 'backend'), 'workspace'],
        [path.join(workspacePath, '..', 'backend'), 'workspace'],
        [path.join(workspacePath, '..', '..', 'backend'), 'workspace']
    ];
    for (const [p, source] of candidates) {
        if (!p) { continue; }
        console.log(`[BACKEND] Checking location: ${p}`);
        if (fs.existsSync(p) && fs.existsSync(path.join(p, 'app', 'main.py'))) {
            return { path: p, source };
        }
    }
    return { path: '', source: '' };
}

function findBackendFolder(workspacePath: string): string {
    return findBackendFolderWithSource(workspacePath).path;
}

// >>> UPDATE-010: accept BOTH `venv` and `.venv`.
// The READMEs historically told users to create `.venv` while this file (and
// start-backend.bat) only accepted `venv` — everyone who followed the docs
// failed the auto-start. `venv` is checked first so existing installs keep
// working unchanged.
export function findVenvPython(backendPath: string): string {
    const relativePaths = process.platform === 'win32'
        ? [['venv', 'Scripts', 'python.exe'], ['.venv', 'Scripts', 'python.exe']]
        : [['venv', 'bin', 'python'], ['.venv', 'bin', 'python']];
    for (const parts of relativePaths) {
        const candidate = path.join(backendPath, ...parts);
        if (fs.existsSync(candidate)) {
            return candidate;
        }
    }
    return '';
}

// >>> UPDATE-010: setup errors are actionable now — no developer-speak
// ("fix the fallback paths in backendManager.ts"), but buttons that lead
// straight to the guide, the setting or a retry.
// >>> UPDATE-012: the first button is the in-VS Code Setup Wizard, which
// can install everything for the user without a terminal.
async function showSetupError(message: string, offerSettings: boolean) {
    const buttons: string[] = ['Run Setup Wizard'];
    if (setupGuideUrl) { buttons.push('Open Setup Guide'); }
    if (offerSettings) { buttons.push('Open Settings'); }
    buttons.push('Retry');
    const pick = await vscode.window.showErrorMessage(message, ...buttons);
    if (pick === 'Run Setup Wizard') {
        await vscode.commands.executeCommand('orion.openSetupWizard');
    } else if (pick === 'Open Setup Guide' && setupGuideUrl) {
        await vscode.env.openExternal(vscode.Uri.parse(setupGuideUrl));
    } else if (pick === 'Open Settings') {
        await vscode.commands.executeCommand('workbench.action.openSettings', 'orion.backendPath');
    } else if (pick === 'Retry') {
        await ensureBackendRunning();
    }
}

export async function ensureBackendRunning() {
    console.log('[BACKEND] === STARTING BACKEND AUTOMATION ===');

    const folders = vscode.workspace.workspaceFolders;

    // >>> FIX 1 (kept): do not return immediately when folders is empty;
    // the setting/env/absolute fallbacks below still apply.
    const workspacePath = folders && folders.length > 0 ? folders[0].uri.fsPath : '';
    console.log(`[BACKEND] Current workspace root: ${workspacePath || '(no workspace)'}`);

    // >>> UPDATE-013 (scope a + b): "port 8000 answers" is no longer treated
    // as "backend ready". The listener is fingerprinted via GET /health and
    // classified: fresh (proceed), stale/orphan/legacy/token-mismatch (offer
    // a restart) or a foreign application (actionable error, no spawn race).
    const inspection = await inspectPort8000(workspacePath);
    switch (inspection.kind) {
        case 'fresh':
            console.log(`[BACKEND] Port 8000 is answered by a FRESH Orion backend (version ${inspection.health.version}, pid ${inspection.health.pid}). Ready.`);
            return;
        case 'foreign':
            console.error('[BACKEND] ❌ Port 8000 is in use by ANOTHER application (it does not answer like an Orion backend).');
            {
                const pick = await vscode.window.showErrorMessage(
                    'Orion: port 8000 is in use by ANOTHER application (it does not answer like an Orion backend). Close that application — or configure it to use a different port — then press Retry.',
                    'Retry'
                );
                if (pick === 'Retry') { await ensureBackendRunning(); }
            }
            return;
        case 'stale':
        case 'legacy-orion':
        case 'token-mismatch':
            {
                const detail = inspection.kind === 'stale'
                    ? `stale backend (${inspection.reason})`
                    : inspection.kind;
                console.warn(`[BACKEND] ⚠️ Port 8000 is answered by a ${detail}; offering a restart.`);
                const pick = await vscode.window.showWarningMessage(
                    inspection.message,
                    'Restart backend now',
                    'Keep using it'
                );
                if (pick !== 'Restart backend now') {
                    console.log('[BACKEND] User kept the existing backend; continuing without restart.');
                    return;
                }
                const freed = await killPort8000();
                if (!freed) {
                    await vscode.window.showErrorMessage(
                        'Orion: could not free port 8000 automatically. Close the process using it manually (Task Manager), then run "Orion: Start Backend" from the Command Palette.',
                        'Open Setup Guide'
                    ).then(p => {
                        if (p === 'Open Setup Guide' && setupGuideUrl) {
                            void vscode.env.openExternal(vscode.Uri.parse(setupGuideUrl));
                        }
                    });
                    return;
                }
                console.log('[BACKEND] Old backend stopped; port 8000 is free — starting the fresh one.');
            }
            break;
        case 'free':
        default:
            console.log('[BACKEND] Port 8000 is free; discovering and starting the backend.');
            break;
    }
    // >>> END OF UPDATE-013 freshness gate

    const backendPath = findBackendFolder(workspacePath);
    if (!backendPath) {
        console.error('[BACKEND] ❌ FAILED: No valid Orion backend folder found in any location.');
        await showSetupError(
            'Orion: backend folder not found. Install it once with the one-line installer (see the setup guide), or point the setting "orion.backendPath" at your existing backend folder.',
            true
        );
        return;
    }
    console.log(`[BACKEND] ✅ FOUND a valid Orion backend at: ${backendPath}`);

    const venvPython = findVenvPython(backendPath);
    console.log(`[BACKEND] Python venv path to be used: ${venvPython || '(none found)'}`);
    if (!venvPython) {
        console.error(`[BACKEND] ❌ FAILED: no python executable found under ${backendPath} (looked for venv/ and .venv/)`);
        await showSetupError(
            `Orion: Python virtual environment not found in ${backendPath}. Run "python -m venv .venv" there, then "pip install -r requirements.txt" — or use the one-line installer from the setup guide.`,
            false
        );
        return;
    }

    const command = `"${venvPython}" -m uvicorn app.main:app --host 127.0.0.1 --port 8000`;
    console.log(`[BACKEND] Running command: ${command}`);

    backendProcess = exec(command, { cwd: backendPath });

    backendProcess.stdout?.on('data', (data) => console.log(`[BACKEND OUT] ${data}`));
    backendProcess.stderr?.on('data', (data) => console.error(`[BACKEND ERR] ${data}`));

    backendProcess.on('error', (err) => {
        console.error('[BACKEND SPAWN ERROR]', err);
        vscode.window.showErrorMessage(`Orion: Failed to start the backend. ${err.message}`);
    });

    // >>> UPDATE-013 (scope b): notice an EARLY EXIT instead of waiting out
    // the full 20 s. The two common causes deserve different messages: the
    // backend crashed during import, or another application grabbed port
    // 8000 between the check above and the bind ("address already in use").
    let exitCode: number | null = null;
    backendProcess.on('exit', (code) => {
        exitCode = code;
        console.warn(`[BACKEND] Backend process exited early with code ${code}.`);
    });

    let attempts = 0;
    while (attempts < 20) {
        const ready = await checkPort(8000);
        if (ready) {
            // >>> UPDATE-013: confirm the answer really is an Orion backend
            // (a foreign app could have won the race for the free port).
            const health = await probeHealth();
            const hb = (health && health.status === 200 ? health.body : null) as HealthInfo | null;
            if (hb && hb.app === 'orion-backend') {
                console.log(`[BACKEND] ✅ Backend is up! (version ${hb.version}, pid ${hb.pid})`);
            } else {
                console.log('[BACKEND] ✅ Port 8000 is up (identity not confirmed via /health).');
            }
            vscode.window.showInformationMessage('Orion: Backend started automatically.');
            return;
        }
        if (exitCode !== null) {
            console.error(`[BACKEND] ❌ FAILED: the backend process exited (code ${exitCode}) before listening on port 8000.`);
            await vscode.window.showErrorMessage(
                `Orion: the backend process exited (code ${exitCode}) before it could listen on port 8000. Common causes: another application grabbed the port at the last moment, or the backend failed during startup — see .orion/logs/orion_stdout.log inside the project folder for the exact error.`,
                'Retry'
            ).then(pick => {
                if (pick === 'Retry') { return ensureBackendRunning(); }
                return Promise.resolve();
            });
            return;
        }
        await new Promise(resolve => setTimeout(resolve, 1000));
        attempts++;
        console.log(`[BACKEND] Waiting for backend... (${attempts}/20)`);
    }

    console.error('[BACKEND] ⚠️ TIMEOUT: Backend responded too slowly.');
    vscode.window.showWarningMessage('Orion: Backend is too slow. Try chatting again in 5 seconds or check the backend terminal manually.');
}

// ---------------------------------------------------------------------------
// >>> UPDATE-012: helpers for the in-VS Code Setup Wizard.
// ---------------------------------------------------------------------------

export interface BackendEnvironment {
    backendPath: string;       // '' when no valid backend folder was found
    backendSource: string;     // 'setting' | 'env' | 'workspace' | ''
    venvPython: string;        // '' when no venv/.venv exists in backendPath
    backendRunning: boolean;   // port 8000 answered
    /** UPDATE-013: identity of whatever answers on port 8000 — null unless a
     *  healthy post-013 Orion backend responded to GET /health. Additive
     *  field: existing wizard code keeps using backendRunning unchanged. */
    backendHealth?: HealthInfo | null;
}

/** One-shot snapshot of everything the wizard needs to render its state. */
export async function detectEnvironment(): Promise<BackendEnvironment> {
    const folders = vscode.workspace.workspaceFolders;
    const workspacePath = folders && folders.length > 0 ? folders[0].uri.fsPath : '';
    const found = findBackendFolderWithSource(workspacePath);
    const running = await checkPort(8000);
    let backendHealth: HealthInfo | null = null;
    if (running) {
        const health = await probeHealth();
        const hb = (health && health.status === 200 ? health.body : null) as HealthInfo | null;
        if (hb && hb.app === 'orion-backend') { backendHealth = hb; }
    }
    return {
        backendPath: found.path,
        backendSource: found.source,
        venvPython: found.path ? findVenvPython(found.path) : '',
        backendRunning: running,
        backendHealth,
    };
}

/** Forcefully free port 8000 so a fresh backend can bind to it.
 *  Returns true when the port is free afterwards. */

// >>> UPDATE-020: runtime stale watchdog ==================================
// ensureBackendRunning() only fingerprints port 8000 at activation / start.
// A `git pull` or an extension update DURING a session silently left the
// OLD backend answering (the exact confusion UPDATE-013 killed at boot,
// still alive mid-session). The watchdog re-classifies every 10 minutes
// and on window focus, and toasts ONCE per backend boot (keyed by
// started_at) with the same Restart action the boot-time dialog offers.
let staleWatchTimer: NodeJS.Timeout | undefined;
let notifiedStaleStartedAt = 0;

export function startStaleWatchdog(): void {
    if (staleWatchTimer) { return; }
    const check = async (): Promise<void> => {
        try {
            const folders = vscode.workspace.workspaceFolders;
            const workspacePath = folders && folders.length > 0 ? folders[0].uri.fsPath : '';
            const inspection = await inspectPort8000(workspacePath);
            if (inspection.kind !== 'stale') { return; }
            const startedAt = inspection.health ? (inspection.health.started_at || 0) : 0;
            if (notifiedStaleStartedAt === startedAt) { return; }  // already toasted for this boot
            notifiedStaleStartedAt = startedAt;
            const pick = await vscode.window.showWarningMessage(
                inspection.message + ' (Detected while you were working — the backend was fresh when this session started.)',
                'Restart backend now',
                'Keep using it'
            );
            if (pick === 'Restart backend now') {
                notifiedStaleStartedAt = 0;
                await restartBackend();
            }
        } catch (e: unknown) {
            console.warn('[BACKEND] stale watchdog check failed (non-fatal):', e);
        }
    };
    staleWatchTimer = setInterval(() => { void check(); }, 10 * 60 * 1000);
    vscode.window.onDidChangeWindowState((st) => {
        if (st.focused) { void check(); }
    });
}
// >>> END OF UPDATE-020 stale watchdog ====================================

export async function killPort8000(): Promise<boolean> {
    // 1. If WE spawned the backend, kill our own child first (cleanest).
    if (backendProcess && !backendProcess.killed) {
        try { backendProcess.kill(); } catch { /* already gone */ }
        backendProcess = null;
    }
    // 2. Then sweep any orphan process still listening on 8000 (the backend
    //    survives VS Code restarts by design — deactivate() is empty).
    const sweeper = process.platform === 'win32'
        ? 'for /f "tokens=5" %a in (\'netstat -ano -p tcp ^| findstr :8000 ^| findstr LISTENING\') do taskkill /F /PID %a'
        : "lsof -ti tcp:8000 -sTCP:LISTEN | xargs -r kill -9";
    await new Promise<void>((resolve) => {
        const shell = process.platform === 'win32' ? 'cmd.exe' : '/bin/sh';
        exec(sweeper, { shell }, () => resolve());
        setTimeout(resolve, 4000); // never hang the wizard on a stuck sweep
    });
    await new Promise(resolve => setTimeout(resolve, 500));
    const stillUp = await checkPort(8000);
    console.log(`[BACKEND] killPort8000: port 8000 ${stillUp ? 'STILL IN USE' : 'free'}`);
    return !stillUp;
}

/** Kill whatever answers on port 8000, then run the normal auto-start. */
export async function restartBackend(): Promise<void> {
    console.log('[BACKEND] === RESTART REQUESTED (Setup Wizard) ===');
    await killPort8000();
    await ensureBackendRunning();
}

import * as vscode from 'vscode';
import { exec, ChildProcess } from 'child_process';
import * as path from 'path';
import * as net from 'net';
import * as fs from 'fs';

let backendProcess: ChildProcess | null = null;

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

    const isReady = await checkPort(8000);
    if (isReady) {
        console.log('[BACKEND] Port 8000 already active. Backend ready.');
        return;
    }

    const folders = vscode.workspace.workspaceFolders;

    // >>> FIX 1 (kept): do not return immediately when folders is empty;
    // the setting/env/absolute fallbacks below still apply.
    const workspacePath = folders && folders.length > 0 ? folders[0].uri.fsPath : '';
    console.log(`[BACKEND] Current workspace root: ${workspacePath || '(no workspace)'}`);

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

    let attempts = 0;
    while (attempts < 20) {
        const ready = await checkPort(8000);
        if (ready) {
            console.log('[BACKEND] ✅ Backend is up!');
            vscode.window.showInformationMessage('Orion: Backend started automatically.');
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
}

/** One-shot snapshot of everything the wizard needs to render its state. */
export async function detectEnvironment(): Promise<BackendEnvironment> {
    const folders = vscode.workspace.workspaceFolders;
    const workspacePath = folders && folders.length > 0 ? folders[0].uri.fsPath : '';
    const found = findBackendFolderWithSource(workspacePath);
    return {
        backendPath: found.path,
        backendSource: found.source,
        venvPython: found.path ? findVenvPython(found.path) : '',
        backendRunning: await checkPort(8000),
    };
}

/** Forcefully free port 8000 so a fresh backend can bind to it.
 *  Returns true when the port is free afterwards. */
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

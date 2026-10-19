import * as vscode from 'vscode';
import { exec, ChildProcess } from 'child_process';
import * as path from 'path';
import * as net from 'net';
import * as fs from 'fs';

let backendProcess: ChildProcess | null = null;

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

export async function ensureBackendRunning() {
    console.log('[BACKEND] === STARTING BACKEND AUTOMATION ===');

    const isReady = await checkPort(8000);
    if (isReady) {
        console.log('[BACKEND] Port 8000 already active. Backend ready.');
        return;
    }

    const folders = vscode.workspace.workspaceFolders;

    // >>> FIX 1: Do not return immediately when folders is empty; try the absolute fallbacks
    let workspacePath = folders && folders.length > 0 ? folders[0].uri.fsPath : '';
    console.log(`[BACKEND] Current workspace root: ${workspacePath || '(no workspace)'}`);

    // >>> FIX 2: Use forward slashes (/) for absolute paths to be safe in Node.js
    const possiblePaths = [
        path.join(workspacePath, 'backend'),
        path.join(workspacePath, '..', 'backend'),
        path.join(workspacePath, '..', '..', 'backend'),
        (process.env.ORION_BACKEND_PATH || '')               // Fallback from env; do NOT hardcode personal paths
    ];

    let backendPath = '';
    for (const p of possiblePaths) {
        console.log(`[BACKEND] Checking location: ${p}`);

        // >>> FIX 3: STRICT validation. Do not only check for a 'backend' folder, 
        // but make sure the file 'app/main.py' exists inside it. This prevents false positives.
        if (fs.existsSync(p) && fs.existsSync(path.join(p, 'app', 'main.py'))) {
            backendPath = p;
            console.log(`[BACKEND] ✅ FOUND a valid Orion backend at: ${backendPath}`);
            break;
        }
    }

    if (!backendPath) {
        console.error('[BACKEND] ❌ FAILED: No valid Orion backend folder found in any location.');
        vscode.window.showErrorMessage('Orion: Orion backend folder not found! Make sure the fallback paths in backendManager.ts are correct.');
        return;
    }

    const venvPython = process.platform === 'win32'
        ? path.join(backendPath, 'venv', 'Scripts', 'python.exe')
        : path.join(backendPath, 'venv', 'bin', 'python');

    console.log(`[BACKEND] Python venv path to be used: ${venvPython}`);

    if (!fs.existsSync(venvPython)) {
        console.error(`[BACKEND] ❌ FAILED: python executable not found at ${venvPython}`);
        vscode.window.showErrorMessage(`Orion: Virtual environment not found. Run 'python -m venv venv' in the backend folder.`);
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
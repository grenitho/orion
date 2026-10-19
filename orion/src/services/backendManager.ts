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
    console.log('[BACKEND] === MEMULAI OTOMATISASI BACKEND ===');

    const isReady = await checkPort(8000);
    if (isReady) {
        console.log('[BACKEND] Port 8000 sudah aktif. Backend siap.');
        return;
    }

    const folders = vscode.workspace.workspaceFolders;

    // >>> PERBAIKAN 1: Jangan langsung return jika folders kosong, coba fallback absolut
    let workspacePath = folders && folders.length > 0 ? folders[0].uri.fsPath : '';
    console.log(`[BACKEND] Workspace root saat ini: ${workspacePath || '(Tidak ada workspace)'}`);

    // >>> PERBAIKAN 2: Gunakan forward slash (/) untuk path absolut agar aman di Node.js
    const possiblePaths = [
        path.join(workspacePath, 'backend'),
        path.join(workspacePath, '..', 'backend'),
        path.join(workspacePath, '..', '..', 'backend'),
        (process.env.ORION_BACKEND_PATH || '')               // Fallback dari env; JANGAN hardcode path pribadi
    ];

    let backendPath = '';
    for (const p of possiblePaths) {
        console.log(`[BACKEND] Mengecek lokasi: ${p}`);

        // >>> PERBAIKAN 3: Validasi KETAT. Jangan hanya cek folder 'backend', 
        // tapi pastikan file 'app/main.py' ada di dalamnya. Ini mencegah false positive.
        if (fs.existsSync(p) && fs.existsSync(path.join(p, 'app', 'main.py'))) {
            backendPath = p;
            console.log(`[BACKEND] ✅ DITEMUKAN backend Orion yang valid di: ${backendPath}`);
            break;
        }
    }

    if (!backendPath) {
        console.error('[BACKEND] ❌ GAGAL: Folder backend Orion yang valid tidak ditemukan di lokasi manapun.');
        vscode.window.showErrorMessage('Orion: Folder backend Orion tidak ditemukan! Pastikan path fallback di backendManager.ts sudah benar.');
        return;
    }

    const venvPython = process.platform === 'win32'
        ? path.join(backendPath, 'venv', 'Scripts', 'python.exe')
        : path.join(backendPath, 'venv', 'bin', 'python');

    console.log(`[BACKEND] Path Python venv yang akan dipakai: ${venvPython}`);

    if (!fs.existsSync(venvPython)) {
        console.error(`[BACKEND] ❌ GAGAL: python.exe tidak ditemukan di ${venvPython}`);
        vscode.window.showErrorMessage(`Orion: Virtual environment tidak ditemukan. Jalankan 'python -m venv venv' di folder backend.`);
        return;
    }

    const command = `"${venvPython}" -m uvicorn app.main:app --host 127.0.0.1 --port 8000`;
    console.log(`[BACKEND] Menjalankan perintah: ${command}`);

    backendProcess = exec(command, { cwd: backendPath });

    backendProcess.stdout?.on('data', (data) => console.log(`[BACKEND OUT] ${data}`));
    backendProcess.stderr?.on('data', (data) => console.error(`[BACKEND ERR] ${data}`));

    backendProcess.on('error', (err) => {
        console.error('[BACKEND SPAWN ERROR]', err);
        vscode.window.showErrorMessage(`Orion: Gagal menjalankan backend. ${err.message}`);
    });

    let attempts = 0;
    while (attempts < 20) {
        const ready = await checkPort(8000);
        if (ready) {
            console.log('[BACKEND] ✅ Backend berhasil menyala!');
            vscode.window.showInformationMessage('Orion: Backend otomatis aktif.');
            return;
        }
        await new Promise(resolve => setTimeout(resolve, 1000));
        attempts++;
        console.log(`[BACKEND] Menunggu backend... (${attempts}/20)`);
    }

    console.error('[BACKEND] ⚠️ TIMEOUT: Backend terlalu lambat merespons.');
    vscode.window.showWarningMessage('Orion: Backend terlalu lambat. Coba chat lagi dalam 5 detik atau cek terminal backend secara manual.');
}
# Project Orion - Fase 2: Functional Chat Panel (v0.1)

## 1. Pencapaian
Panel Chat Webview berhasil dibuat dan terhubung penuh dengan Backend Python.
User dapat mengetik perintah di panel, dan AI (DeepSeek via Sumopod) membalas secara real-time.

## 2. Teknologi & Implementasi
- **Webview API:** Menggunakan `vscode.WebviewPanel` untuk merender UI HTML/CSS/JS kustom.
- **Komunikasi Dua Arah:** 
  - UI -> Extension: `vscode.postMessage()`
  - Extension -> UI: `webview.postMessage()`
- **Styling:** Menggunakan CSS Variables bawaan VS Code (`var(--vscode-...)`) agar tema panel mengikuti tema editor (Dark/Light mode otomatis).

## 3. Konfigurasi
- Command `orion.openChat` terdaftar di `package.json`.
- Backend Service (`setProject` & `chat`) dipanggil dari dalam class `ChatPanel`.
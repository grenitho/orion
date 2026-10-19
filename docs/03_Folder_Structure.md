# Project Orion - Struktur Folder & Inisialisasi Proyek (v0.1)

## 1. Pemisahan Arsitektur (Separation of Concerns)
Proyek dibagi menjadi dua entitas utama yang berjalan terpisah namun berkomunikasi via HTTP/WebSocket:
- **`extension/`**: Berbasis TypeScript/Node.js. Bertugas sebagai jembatan antara UI VS Code, VS Code API (File System, Terminal), dan Backend Python.
- **`backend/`**: Berbasis Python (FastAPI). Bertugas sebagai "Otak Orkestrasi" yang mengelola konteks, memanggil AI Gateway (LiteLLM), dan mengeksekusi *tools*.

## 2. Struktur Direktori Utama
- `docs/`: Berisi semua dokumen desain, arsitektur, dan keputusan proyek.
- `extension/`: Berisi kode sumber VS Code Extension (`src/`, `package.json`).
- `backend/`: Berisi kode sumber FastAPI. Logika utama dipecah di dalam `app/services/`:
  - `ai_gateway.py`: Menangani multi-model routing dan auto-fallback.
  - `context_manager.py`: Menangani memori percakapan dan ekstraksi struktur folder.
  - `tool_executor.py`: Menjalankan fungsi fisik (baca/tulis file, eksekusi terminal).
- `scripts/`: Berisi script otomatisasi (seperti `start_dev.bat`) untuk menjalankan kedua server secara bersamaan demi kemudahan UX.

## 3. Manajemen Secrets (API Keys)
- API Keys untuk provider AI (Gemini, Groq, OpenRouter, dll) **HANYA** disimpan di file `backend/.env`.
- File `.env` WAJIB dimasukkan ke dalam `.gitignore` agar tidak ter-commit ke repository publik.
- VS Code Extension tidak perlu menyimpan API Key; ia cukup mengirim prompt ke Backend, dan Backend yang akan menyuntikkan (inject) API Key saat memanggil provider AI.
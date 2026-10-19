# Project Orion - Backend Milestone 1: AI Connection Established

## 1. Status Koneksi
Backend Python (FastAPI) berhasil terhubung ke Google AI (Gemini) melalui LiteLLM.
Endpoint `/test-ai` berhasil memverifikasi bahwa API Key valid dan server dapat menerima respons teks dari model AI.

## 2. Pemilihan Model Final untuk Development
Meskipun model eksperimental (seperti 3.5-flash) dapat terhubung, untuk stabilitas **Function Calling (JSON Output)** dan **Agentic Loop**, model berikut direkomendasikan:
- **Primary:** `gemini/gemini-3.5-flash` atau `gemini/gemini-3-flash` (Sangat stabil, context window besar, hemat token).
- **Fallback:** `gemini/gemini-3-flash` (Untuk penalaran yang lebih dalam jika diperlukan).

## 3. Konfigurasi Saat Ini
- Framework: FastAPI + Uvicorn
- AI Gateway: LiteLLM
- Environment: `.env` (Menyimpan API Key dan DEFAULT_MODEL)
- Port Server: 8000
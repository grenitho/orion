# Project Orion - Rencana Integrasi chat2api (v0.2)

## 1. Tujuan
Menambahkan dukungan untuk model Qwen dan DeepSeek menggunakan akun pribadi melalui proxy `chat2api`.

## 2. Cara Kerja
- `chat2api` akan di-hosting secara lokal atau di server pribadi.
- `chat2api` akan menerjemahkan permintaan OpenAI-compatible menjadi permintaan ke API Qwen/DeepSeek.
- Di Orion Backend (`.env`), kita akan menambahkan `OPENAI_API_BASE` yang mengarah ke localhost `chat2api`.
- LiteLLM akan memperlakukan endpoint ini sebagai provider OpenAI biasa, sehingga fitur Auto-Fallback dapat langsung berjalan.
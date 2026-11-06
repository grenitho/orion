# PROGRESS — Perbaikan & Pengembangan Orion

> File ini adalah memori eksternal. Setiap sesi baru, tempel isi file ini
> ke asisten AI (Qwen, Gemini, atau Claude) agar ia tahu di mana kita
> berada. Update file ini di akhir setiap sesi.

## Environment
- OS: Windows 11 (D:\APP Grenitho\orion-project)
- Shell: PowerShell
- Python: 3.11.9 ✅
- Node: v24.19.0 ✅
- git: 2.54.0.windows.1 ✅
- venv: `backend\venv` (aktif saat perintah dijalankan)
- Backend version: 0.10.44 (dari GET /health via GET /)
- Extension version: 0.4.5 (dari package.json — akan diverifikasi)
- Test baseline: **663 passed, 1 skipped** (135 detik)
- Branch: main, up-to-date dengan origin

## Status Fase
- [x] P0 — Verifikasi Baseline ✅ (2026-11-05)
- [ ] P1 — Audit Fitur Inti (attach, delegasi, MCP)
- [ ] P1.5 — Perbaiki guard MCP
- [ ] P2 — Bug Cepat & UX
- [ ] P3 — Refactor agent_loop.py
- [ ] P4 — Sandbox Eksekusi
- [ ] P5 — Akses GitHub Terbatas
- [ ] P6 — Pencarian Semantik
- [ ] P7 — Pusat Konfigurasi
- [ ] P8 — Non-Coding Tasks (eksperimental)
- [ ] P9 — UI di Luar VS Code (vision)
- [ ] P9.0 — Hardening Multi-Sesi (prasyarat P9)

## Log Sesi

### Sesi 0 — 2026-11-05 — Penyusunan Blueprint
**Tujuan:** Sepakati rencana kerja
**Hasil:**
- ✅ Blueprint dan PROGRESS disusun
- ✅ Prioritas dikoreksi: fitur inti dulu, refactor menyusul
- ✅ Masukan AI lain dievaluasi: P1.5 (guard MCP) dan P9.0 (multi-sesi) ditambahkan
**Berikutnya:** P0 — Verifikasi Baseline

### Sesi 1 — 2026-11-05 — Verifikasi Baseline (P0)
**Tujuan:** Pastikan environment sehat
**Hasil:**
- ✅ Python 3.11.9, Node v24.19.0, git 2.54.0 — semua memenuhi syarat
- ✅ Test suite: **663 passed, 1 skipped** — hijau, di atas baseline dokumentasi (393)
- ✅ Backend jalan, versi 0.10.44
- ✅ Branch main, up-to-date
- ⚠️ File untracked: `backend/.playwright-mcp/` — perlu masuk `.gitignore`
- ⚠️ Dokumentasi menyebut 393 test, kenyataannya 663 — perlu diupdate (P2)
**Kendala:** Tidak ada blocker
**Berikutnya:** P1.1 — Audit Attach File

### Sesi 2 — 2026-11-05 — E1 Smoke Test Attach File (P1.1 Awal)
**Tujuan:** Uji pipeline attach untuk 5 jenis file
**Hasil:**
- ✅ txt: LULUS, tersalin ke `.orion/attachments/test.txt`
- ❌ **png: GAGAL, model "cannot see image", file TIDAK tersalin**
- ✅ pdf: LULUS, tersalin (1369 byte)
- ✅ docx: LULUS, tersalin (36594 byte)
- ✅ xlsx: LULUS, tersalin (4879 byte)

**Temuan:**
- **BUG-1 (Kritis):** PNG tidak terbaca model. Diagnosis sedang berjalan.
- **BUG-2 (Sedang):** PNG tidak disalin ke `.orion/attachments/` — inkonsistensi dengan tipe lain. Hilang setelah turn berikutnya.
- Catatan: model berhasil membaca ulang `.orion/attachments/test.txt` di turn berikutnya (via `read_file`) — memori jangka pendek BEKERJA untuk teks.

**Berikutnya:** Diagnosis PNG (log + versi litellm)

### Sesi 3 — 2026-11-06 — P1.1 Audit Attach File (Selesai)
**Tujuan:** Uji pipeline attach untuk 5 jenis file & perbaiki bug.
**Hasil:**
- ✅ txt, pdf, docx, xlsx: Lulus (tersalin ke `.orion/attachments/`).
- ✅ **Perbaikan Bug 1 (Vision PNG):** Memperbaiki format injeksi `api_messages` di `agent_loop.py` agar gambar terkirim ke LLM.
- ✅ **Perbaikan Bug 2 (Memori PNG):** Menambahkan `_simpan_referensi` di `attachments.py` agar gambar disalin ke disk dan bisa dibaca setelah restart.
- ✅ Memori jangka pendek (E2) dan pasca-restart (E3) terverifikasi bekerja.
- ⚠️ **Temuan Baru (UI/UX):** Model (Flash Lite) sering membocorkan monolog bahasa Inggris dan halusinasi "file berubah" saat menjawab. (Masuk ke antrean P2 — UX & Prompt).

**Berikutnya:** P1.2 Audit Delegasi Sub-Agent.

### Sesi 4 — 2026-11-06 — P1.2 Audit Delegasi Sub-Agent (Selesai)
**Tujuan:** Uji apakah model benar-benar memanggil sub-agent.
**Hasil:**
- ✅ Explorer berfungsi (menemukan fungsi di 3 file).
- ✅ Verifier berfungsi (memberikan vonis PASS).
- ✅ Workers berfungsi (membuat 2 file paralel dengan benar).
- ⚠️ **Temuan UX Kritis:** Model sering bocor mulut (monolog bahasa Inggris & bocor instruksi sistem) ke UI. Memori chat juga cepat penuh ("Memory full, compressing context").
- ⚠️ **Temuan Konfigurasi:** Ditemukan file `.orion/orion_config.json` di suatu tempat dengan setting ekstrem (`max_iterations: 500`, `subagent_max_per_turn: 30`). Ini berbahaya untuk kuota free tier. (Perlu investigasi di P2).

**Berikutnya:** P1.3 Audit MCP Playwright.

### Sesi 5 — 2026-11-06 — P1.3 Audit MCP Playwright (LULUS)
**Tujuan:** Uji apakah Orion bisa menggunakan Playwright MCP.
**Hasil:**
- ✅ **Playwright MCP Bekerja Sempurna:** Berhasil membuka `localhost:8080`, mengambil teks (9988), dan mengevaluasi `getComputedStyle` (rgb(255,0,0)).
- ✅ **Fitur Ledger Gaya (Computed Style Gate) bekerja:** Output browser disuntikkan ke layar oleh kode backend, bukan klaim halusinasi model.
- Catatan: MCP harus diinstal per-project (by design).
- ✅ **P1 FASE SELESAI:** Semua fitur inti (Attach, Sub-Agent, MCP) LULUS.

### Sesi 6 — 2026-11-06 — P2 Bug Cepat & UX (Selesai)
**Tujuan:** Membungkam monolog bahasa Inggris yang bocor ke chat.
**Hasil:**
- ✅ **Filter Monolog:** Menambahkan blokir pola "Let's verify", "The user's instruction", dll. di `stream_utils.py` (fungsi `catatan_untuk_user`). Model sekarang menjawab bersih tanpa membocorkan instruksi sistem.
- ⚠️ **Pending (P2):** Investigasi file `.orion/orion_config.json` yang memiliki setting `max_iterations: 500` (bisa menghabiskan kuota). (Akan dilakukan nanti saat masuk P7).

**Berikutnya:** P3 — Refactor agent_loop.py (Opsional) atau P1.5 — Guard MCP.

### Sesi 7 — 2026-11-06 — P1.5 Perbaiki Guard MCP (Selesai)
**Tujuan:** Menutup celah bypass guard pada MCP tool.
**Hasil:**
- ✅ `tool_guards.py`: Mengubah `pre_call_check` dari sekadar *notice* menjadi *[MCP HARD BLOCK]* untuk nama tool yang memodifikasi state (write/delete/exec).
- ✅ `agent_loop.py`: Menambahkan pengecekan agar tool yang diblokir tidak dieksekusi.
- ✅ Terverifikasi: Orion menolak memanggil tool fiktif berbahaya.

**Berikutnya:** P4 — Sandbox Eksekusi.

### Sesi 8 — 2026-11-06 — P4 Sandbox Eksekusi Tahap 1 (Selesai)
**Tujuan:** Mencegah eksekusi perintah destruktif dan RCE via prompt injection.
**Hasil:**
- ✅ Menambahkan fungsi `_validasi_sandbox` di `tool_executor.py`.
- ✅ Mencegat perintah pemformatan disk (format, rd /s), RCE (curl | bash), dan bypass directory (cd ..).
- ✅ Terverifikasi: Orion menolak menjalankan `curl | bash` dan melaporkan blokir keamanan ke user.

**Berikutnya:** P3 — Refactor agent_loop.py atau P5 — Akses GitHub.

### Sesi 9 — 2026-11-06 — P3 Refactor & Finalisasi Keamanan (Selesai)
**Tujuan:** Memisahkan utilitas teks dari agent_loop dan memastikan semua test hijau.
**Hasil:**
- ✅ `text_cleaner.py` dibuat: memisahkan fungsi pembersih teks dan estimasi token.
- ✅ `tool_guards.py`: Menerapkan blokir keras (HARD BLOCK) untuk MCP tool berbahaya.
- ✅ `agent_loop.py`: Diperbaiki dari error referensi variabel.
- ✅ **Test Suite: 663 passed, 1 skipped.** 100% hijau.

**Berikutnya:** P5 — Akses GitHub Terbatas.

## Catatan Penting
- DNA Orion 100% dari Qwen; Qwen free tier sekarang ketat → asisten cadangan dibutuhkan
- Model utama: Gemini Flash Lite ×4 key + fallback OpenRouter/Cloudflare
- Proyek uji: InvoiceFlow
- Misi: membantu pemula belajar coding gratis + disclaimer kualitas model gratis
- **Baseline test adalah 663 passed, bukan 393** — dokumentasi basi, akan dikoreksi
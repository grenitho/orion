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
- Test baseline: **663 passed, 1 skipped** (142 detik)
- Branch: main, up-to-date dengan origin

## Status Fase
- [x] P0 — Verifikasi Baseline ✅ (2026-11-05)
- [x] P1 — Audit Fitur Inti (attach, delegasi, MCP)
- [x] P1.5 — Perbaiki guard MCP
- [x] P2 — Bug Cepat & UX
- [x] P3 — Refactor agent_loop.py
- [x] P4 — Sandbox Eksekusi
- [x] P5 — Akses GitHub Terbatas
- [x] P6 — Pencarian Semantik
- [x] P7 — Pusat Konfigurasi ✅ (P7.1, P7.2, P7.3, P7.4 selesai)
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

### Sesi 10 — 2026-11-06 — P5 Akses GitHub (Selesai)
**Tujuan:** Menghubungkan Orion dengan GitHub secara aman.
**Hasil:**
- ✅ `github_manager.py` dibuat: membungkus `gh` CLI (list_issues, create_pr, get_repo_info).
- ✅ Tool didaftarkan di schema, dispatch, dan executor.
- ✅ Terverifikasi: Orion berhasil membaca info repo (grenitho/orion) langsung dari GitHub.
- ✅ Perbaikan bug: `gh.exe` di Windows kini dicari secara eksplisit jika VS Code menyembunyikan PATH.

### Sesi 11 — 2026-11-07 — P6.1 Prototipe Semantic Search (Selesai)
**Tujuan:** Buktikan konsep pencarian berbasis makna (embedding) sebelum integrasi.
**Hasil:**
- ✅ Dependency baru terpasang di venv: `sentence-transformers`, `sqlite-vec`, `torch 2.15.0+cpu`.
- ✅ Prototipe di `scripts/semantic_search_proto.py` + `scripts/query_semantic_search.py`.
- ✅ Model `BAAI/bge-small-en-v1.5` (384 dim) — jauh lebih akurat untuk kode daripada `all-MiniLM-L6-v2`.
- ✅ Indexing 52 file → 770 chunk ke `.orion/semantic_index.db`.
- ✅ Uji kualitas: query "backup file sebelum menimpa" → `tool_executor.py` (3/5 hasil, #1 skor tertinggi); "UI panel chat" → `ChatPanel.ts` (3/5).
- ⚠️ **Pelajaran iterasi:**
  - Indexing seluruh file (bukan chunk) → noise dari file besar (`BLUEPRINT.md`, `agent_loop.py`).
  - Hybrid search saja tidak cukup jika model embedding lemah.
  - Chunking per-fungsi + model BGE + hanya file kode = kombinasi yang bekerja.
**Berikutnya:** P6.3-A — modul backend.

### Sesi 12 — 2026-11-07 — P6.3-A Modul Semantic Search (Selesai)
**Tujuan:** Bungkus prototipe menjadi modul Python yang bisa diimpor backend.
**Hasil:**
- ✅ `backend/app/services/semantic_search.py` dibuat: class `SemanticSearchEngine` dengan `index_project()` + `search()`.
- ✅ Failure contract: semua error → string `[SEMANTIC_SEARCH ...]`, tidak pernah crash.
- ✅ Soft-import: library hilang = fitur mati sopan, backend tetap boot.
- ✅ Index tersimpan di `<project>/.orion/semantic_index.db` (bukan di `scripts/`).
- ✅ Dependensi ditambahkan ke `backend/requirements.txt`.
- ✅ Verifikasi: test modul mandiri menghasilkan output identik dengan prototipe.
**Berikutnya:** P6.3-B — daftarkan tool ke Orion.

### Sesi 13 — 2026-11-07 — P6.3-B Integrasi Tool (Selesai)
**Tujuan:** Daftarkan `semantic_search` + `index_semantic_search` sebagai tool agent.
**Hasil:**
- ✅ `tools_schema.py`: 2 schema tool baru (deskripsi jujur tentang kapan dipakai).
- ✅ `tool_executor.py`: 2 method wrapper + lazy-init `_semantic_engine` di `__init__`.
- ✅ `tool_dispatch.py`: 2 entry di `TABLE` + `NAMA_TOOL_AKTIF`.
- ✅ `main.py`: reset engine di `/set-project` (dipindah ke luar `if todo_manager:` — safety).
- ✅ `prompts.py`: instruksi kapan pakai `semantic_search` vs `search_code`.
- ⚠️ **Bug arsitektural P5 ditemukan & diperbaiki:** 3 tool GitHub terdaftar di `TABLE` DAN `WAJIB_DI_MAIN` sekaligus — kontradiksi. Solusi: hapus dari `WAJIB_DI_MAIN` (mereka sudah berjalan via jalur sederhana sejak P5).
- ✅ Test suite: **663 passed, 1 skipped**.
**Berikutnya:** P6.3-C — uji end-to-end.

### Sesi 14 — 2026-11-07 — P6.3-C Uji End-to-End (Selesai)
**Tujuan:** Buktikan Orion benar-benar memanggil tool semantik dalam percakapan.
**Hasil:**
- ✅ Chat "sebutkan tool" → `semantic_search` + `index_semantic_search` muncul.
- ✅ Chat "lakukan indexing semantik" → `[SEMANTIC_INDEX OK] 52 file, 774 chunk`.
- ✅ Chat "cari backup file sebelum ditimpa" → `tool_executor.py` fungsi `_backup_before_overwrite` (baris 235).
- ✅ Chat "di mana verifikasi sintaks Python" → `code_verifier.py` + `tool_executor.py`.
- ⚠️ **Bug UX ditemukan di guard:** pertanyaan informasi ("di file mana itu diimplementasikan?") memicu debt `[implementasi]` palsu karena kata kerja mutasi ("diimplementasikan") ada di pertanyaan.
- ✅ **Perbaikan:** `guard_rules.adalah_pertanyaan()` + early-return di `_perlu_tantang_klaim_implementasi` dan `_perlu_tantang_nihil_mutasi`. Regex diiterasi 2× (butuh `\b` dan `di\s*mana` tanpa batas) sampai false-negative `"di file mana..."` hilang.
- ✅ Verifikasi akhir: 663 passed; chat ulang bersih dari debt.
**Berikutnya:** P6.4 — indexing incremental (file berubah saja).

## Catatan Penting (diperbarui 2026-11-07)
- **SELALU aktifkan venv** sebelum `python`/`pip`/`pytest`: `venv\Scripts\activate`. Tanpa `(venv)` di prompt, hasil test menyesatkan (kejadian Sesi 14: `mcp` "hilang" padahal hanya salah interpreter).
- **P6 semantic search:** model `BAAI/bge-small-en-v1.5` (384-dim), index di `.orion/semantic_index.db`. Indexing pertama ~5-10 menit di CPU; query <1 detik.
- **Pelajari pola kegagalan:** test suite 663 test menangkap 1 `NameError` → 40 test gagal berantai. Root cause tunggal: dead code setelah `return` di `guard_rules.py`. Selalu baca error pertama di traceback, bukan yang terakhir.

### Sesi 15 — 2026-11-07 — P6.4 Indexing Incremental (Selesai)
**Tujuan:** Hindari re-indexing seluruh proyek setiap kali; hanya file berubah.
**Hasil:**
- ✅ `SemanticSearchEngine.index_project(force=False)` sekarang inkremental.
- ✅ Tabel baru `indexed_files(path, mtime, size)` menyimpan sidik per file.
- ✅ File tidak berubah (mtime+size sama) dilewati; file berubah → chunk lama dihapus lalu re-index; file hilang → metadata + chunk dihapus.
- ✅ Uji: run-1 `new:54`, run-2 `unchanged:54`; touch file → `updated:1`; revert → `updated:1` (mtime berubah).
- ✅ Test suite: **663 passed, 1 skipped**.
- Catatan: index pertama dari prototype lama memakai 52 file (saat `scripts/` belum di-ignore) — kini 54 karena folder `scripts/` di-ignore.
**Berikutnya:** P7 — Pusat Konfigurasi (opsional), atau P6.5 — Integrasi auto-indexing di `/set-project` dan pasca-edit (jika diinginkan).

### Sesi 16 — 2026-11-07 — P7.1 Config Endpoint (Selesai)
**Tujuan:** Endpoint tunggal `GET/POST /config` untuk `.orion/orion_config.json`.
**Hasil:**
- ✅ `GET /config`: mengembalikan snapshot konfigurasi, default, range, dan `env_summary` (jumlah key, tanpa nilai rahasia).
- ✅ `POST /config`: validasi ketat (range, tipe data, key asing), atomic write, dan live sync ke `orion_config` global tanpa restart backend.
- ✅ 9 test baru di `test_config_endpoint.py`; test suite: 672 passed.

### Sesi 17 — 2026-11-07 — P7.2 Config Center Panel & Rilis 0.5.0 (Selesai)
**Tujuan:** Panel UI baru `ConfigCenterPanel.ts` dengan tab Project.
**Hasil:**
- ✅ Panel tab-based dengan form Project (max_iterations, token_budget, subagents, inbox, mcp_enabled).
- ✅ Tombol Save (atomic + live sync), Reset to defaults, dan Refresh.
- ✅ Rilis **Extension 0.5.0** (VSIX) dan Release Note GitHub disesuaikan dengan pola rilis sebelumnya. Menambahkan catatan disiplin rilis (VSIX + Marketplace) di dokumen ini.

### Sesi 18 — 2026-11-07 — P7.3 Config Center: Model, MCP, Sub-agent Tabs (Selesai)
**Tujuan:** Memoles tab Model, MCP, dan Sub-agent agar ramah pengguna awam.
**Hasil:**
- ✅ Tab Model: status card menampilkan jumlah key Gemini, status tier (ACTIVE/DISABLED), default model, dan provider group.
- ✅ Tab MCP: status card menampilkan master switch (ON/OFF) dan jumlah server terhubung, dengan tombol Refresh Status.
- ✅ Tab Sub-agent: menampilkan profil model per-role (Explorer/Verifier/Workers) atau "Main Router (default)".
- ✅ Tab Data: menampilkan path file lokal (.orion, session_history, semantic_index, logs).
- ✅ Verifikasi visual: semua tab tampil rapi, data akurat (4 Gemini keys, 2 MCP servers connected).

**Status P7:** ✅ SELESAI.
**Berikutnya:** P8 — Non-Coding Tasks (eksperimental, TBD) atau P9 — UI di Luar VS Code (vision, post-1.0).

**Berikutnya:** Panduan GitHub untuk Pengguna Lain (Dokumentasi) & P6 Pencarian Semantik.

## Catatan Penting
- DNA Orion 100% dari Qwen; Qwen free tier sekarang ketat → asisten cadangan dibutuhkan
- Model utama: Gemini Flash Lite ×4 key + fallback OpenRouter/Cloudflare
- Proyek uji: InvoiceFlow
- Misi: membantu pemula belajar coding gratis + disclaimer kualitas model gratis
- **Baseline test adalah 663 passed, bukan 393** — dokumentasi basi, akan dikoreksi
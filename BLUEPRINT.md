# BLUEPRINT — Peta Jalan Orion

> Dokumen ini adalah rencana besar. Tidak berubah kecuali kita sepakat
> untuk mengubahnya. Untuk status dinamis, lihat PROGRESS.md.

## Prinsip Dasar
1. Free tier first — setiap komponen baru harus gratis / open-source
2. Fitur yang sudah ada harus benar-benar bekerja sebelum menambah fitur baru
3. Satu sesi = satu tugas terverifikasi
4. Semua progres tercatat di PROGRESS.md
5. Verifikasi setiap langkah; backup sebelum ubah
6. Misi: membantu pemula belajar coding gratis + disclaimer kualitas model gratis

## Prioritas & Fase

### P0 — Verifikasi Baseline (1 sesi)
Tujuan: pastikan environment sehat, test hijau, backend jalan.
Langkah:
1. Cek versi Python, Node, git
2. Buat venv bersih, install dependensi
3. Jalankan test suite: catat baseline
4. Jalankan backend, cek GET /
5. Isi bagian Environment di PROGRESS.md
Kriteria sukses: test 0 failed; backend responsif; baseline tercatat.

### P1 — Audit Fitur Inti (6-8 sesi)
Tujuan: buktikan fitur yang sudah ada benar-benar bekerja, perbaiki yang rusak.

#### P1.1 — Audit Attach File (2 sesi)
Pertanyaan yang harus dijawab:
- Jenis file apa yang benar-benar terbaca? (teks, gambar, pdf, docx, xlsx)
- File disimpan di mana? Berapa lama bertahan?
- Apakah ada batas ukuran yang wajar?
- Apakah model benar-benar "melihat" isi file?
Langkah:
- Baca `backend/app/services/attachments.py` dan `inbox_manager.py`
- Buat test manual: lampirkan 1 file per jenis, tanya model tentang isinya
- Catat temuan; perbaiki yang rusak
- Tulis dokumentasi singkat untuk pengguna

#### P1.2 — Audit Delegasi Sub-Agent (2 sesi)
Pertanyaan yang harus dijawab:
- Apakah Explorer, Verifier, Worker benar-benar dipanggil model?
- Apakah hasilnya masuk ke main loop dengan benar?
- Apakah budget (`subagent_max_per_turn`) bekerja?
- Apakah ada bug di `subagent_manager.py`?
Langkah:
- Baca `backend/app/services/subagent_manager.py`
- Buat test manual dengan prompt yang memicu delegasi
- Cek log backend: apakah sub-agent benar-benar jalan?
- Perbaiki yang rusak
- Dokumentasikan cara memverifikasi delegasi bekerja

#### P1.3 — Audit MCP Playwright (2 sesi)
Pertanyaan yang harus dijawab:
- Apakah Playwright MCP terdeteksi dan tools-nya terdaftar?
- Apakah model benar-benar bisa navigate/click/evaluate?
- Apakah computed-style gate bekerja?
Langkah:
- Cek `GET /mcp/status` → apakah playwright "ok"?
- Buat test manual: minta Orion buka localhost:3000 dan baca judul halaman
- Cek log: apakah `mcp__playwright__browser_navigate` dipanggil?
- Perbaiki yang rusak
- Dokumentasikan contoh prompt yang memicu pemakaian browser

#### P1.4 — Audit MCP Lain (0-2 sesi, tergantung MCP yang dipasang)
Sama seperti P1.3, untuk MCP selain Playwright.

### P1.5 — Perbaiki Guard MCP (1-2 sesi)
Tujuan: tutup celah bypass guard pada jalur mcp__.
Langkah:
- Perkuat `tool_guards.pre_call_check` menjadi fungsi yang bisa
  mengembalikan (allow | confirm | deny).
- Jika tool MCP namanya mengandung write/delete/exec/move/kill:
  → paksa confirm via modal VS Code (reuse `confirm_terminal`).
- Tambahkan field `risk: "read-only" | "writable"` di mcp.json per server.
- Update `docs/MCP_FOUNDATION.md` dengan peringatan keamanan.
Kriteria: MCP tool yang berpotensi menulis tidak bisa dieksekusi tanpa konfirmasi.

### P2 — Bug Cepat & UX Onboarding (2 sesi)
Bug yang sudah diidentifikasi:
- Escaping `\\u00d7` di `chatWebview.ts` → `\u00d7`
- Kode mati `$savedEnv` di `install.ps1`
- Versi extension vs backend tidak ada mekanisme kecocokan
UX:
- Modal persetujuan lebih mendidik (label risiko)
- Disclaimer kualitas model gratis di welcome screen

### P3 — Refactor agent_loop.py (3-4 sesi)
Tujuan: kurangi kompleksitas, pisahkan logika kebijakan.
Langkah:
- P3.1 — Inventarisasi & desain modul baru
- P3.2 — Ekstrak `challenge_engine.py`
- P3.3 — Ekstrak `debt_ledger.py`
- P3.4 — Ekstrak `stagnation_detector.py` + cleanup
Kriteria: agent_loop.py turun ~50%; baseline SSE byte-identik.

### P4 — Sandbox Eksekusi (4-6 sesi)
Tools gratis:
- Linux: bubblewrap (bwrap) atau firejail
- Windows: AppContainer via pywin32, atau Windows Sandbox (Pro)
- macOS: sandbox-exec
Langkah:
- P4.1 — Desain API `SandboxRunner` + prototipe Linux
- P4.2 — Fallback & graceful degradation
- P4.3 — Integrasi ke `run_terminal`
- P4.4 — Windows/macOS (opsional, defer jika kompleks)
- P4.5 — Modal persetujuan lebih mendidik

### P5 — Akses GitHub Terbatas (3-4 sesi)
Tujuan: Orion bisa membaca/commit ke repo yang diizinkan.
Tools gratis: `PyGithub` (MIT) atau git CLI.
Langkah:
- P5.1 — Desain: allowlist repo, token per-repo, izin baca/tulis
- P5.2 — Tool baru: `github_list_issues`, `github_read_pr`, `github_create_pr`
- P5.3 — Tool baru: `github_commit` (dengan konfirmasi ganda)
- P5.4 — UI: panel untuk manage repo allowlist

### P6 — Pencarian Semantik (3-4 sesi)
Stack gratis: sentence-transformers + SQLite + sqlite-vec
Langkah:
- P6.1 — Prototipe indexing
- P6.2 — Query API
- P6.3 — Integrasi sebagai tool
- P6.4 — Indexing incremental

### P7 — Pusat Konfigurasi (2-3 sesi)
Tujuan: semua setting non-rahasia bisa diubah dari UI.
Langkah:
- P7.1 — Desain skema terpadu
- P7.2 — Backend: migrasi & endpoint /config
- P7.3 — Extension: panel pengaturan

### P8 — Non-Coding Tasks (eksperimental, TBD)
Tujuan: surat, dokumen, big data, sosmed/marketplace.
Catatan: ini di luar DNA coding Orion. Butuh modul baru, kemungkinan
menggunakan MCP server spesifik per domain (mis. MCP untuk Google Docs,
MCP untuk analisis data, MCP untuk sosmed). Akan dirinci setelah P7 selesai.

### P9 — UI di Luar VS Code (vision, post-1.0)
Tujuan: interface standalone tanpa VS Code.
Pendekatan: TUI (textual) atau Web UI (FastAPI + HTML) sebagai klien
baru atas API yang sudah ada. Bukan rewrite; hanya frontend baru.

### P9.0 — Hardening Multi-Sesi (prasyarat P9, TBD)
Tujuan: jika Orion diintegrasikan ke Telegram/WhatsApp/UI standalone,
pastikan dua percakapan simultan tidak saling menimpa state.

Masalah saat ini:
- `context_manager` adalah satu instance per proyek → 2 request /chat
  paralel bisa race saat save_history.
- `SessionState` aman (per-request), tapi `context_manager` tidak.

Solusi yang akan dieksplorasi:
- `session_id` (UUID) per percakapan; `context_manager` jadi dict.
- File `.orion/sessions/<session_id>.json` per percakapan.
- Endpoint `/chat` menerima header `X-Orion-Session-Id`.

Akan dirinci saat P9 dimulai.

## Yang TIDAK Dikerjakan (dan Alasannya)
- Cloud sync / multi-user — di luar visi single-user
- Streaming token dari LLM — sudah ditolak desain
- Rewrite ke framework lain — hilangkan kontrol yang sudah baik
- Database server (Postgres) — SQLite cukup

## Protokol Kerja
1. Setiap sesi mulai dengan tempel isi PROGRESS.md
2. Satu sesi = satu tugas terverifikasi
3. Backup file sebelum ubah: `cp file.py file.py.bak`
4. Verifikasi sebelum lanjut
5. Update PROGRESS.md di akhir sesi
6. Jika AI kehilangan konteks: tempel BLUEPRINT.md + PROGRESS.md
7. Jangan pernah nonaktifkan modal persetujuan
8. Selalu jalankan test suite setelah perubahan
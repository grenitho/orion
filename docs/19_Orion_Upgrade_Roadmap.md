# 🚀 Rencana Upgrade Diri Orion — Roadmap Fitur & Fungsi

> **Dokumen**: Roadmap fitur-fitur upgrade Orion berdasarkan rencana pengembangan diri.
> **Status**: Draft / Backlog
> **Tanggal**: Mengikuti sprint aktif

---

## 🎯 Prioritas yang Disarankan

1. **Streaming Respons** (#1) — sudah menjadi TODO resmi & paling berdampak.
2. **Tool `edit_file` + `search_code`** (#6, #7) — paling sering dibutuhkan untuk efisiensi.
3. **Auto-Sync Konteks** (#12) — menjaga dokumentasi selalu akurat.
4. **Self-Test & Unit Test** (#20) — menjamin kualitas setelah pengembangan.

---

## A. 🧠 Kecerdasan Inti (Core Intelligence)

| No | Fitur | Deskripsi & Manfaat |
|----|-------|---------------------|
| 1 | **Streaming Respons** | Mengalirkan token respons secara real-time (SSE) agar respons panjang tidak terpotong `max_tokens`. Sudah jadi TODO di sprint. |
| 2 | **Context Window Cerdas (Pintar)** | Trimming/summarize otomatis riwayat chat ketika melebihi batas token, agar konteks tetap relevan tanpa kehilangan memory penting. |
| 3 | **Multi-Session Memory** | Menyimpan memory per-project dan per-workspace, bukan satu riwayat global. Jadi bisa berpindah project tanpa kehilangan konteks. |
| 4 | **Autonomous Task Planning** | Membuat rencana langkah (step-by-step plan) sebelum mengeksekusi tugas besar, lalu mengikuti dan melaporkan progresnya. |
| 5 | **Self-Reflection / Code Review** | Setelah menyelesaikan tugas, otomatis meninjau ulang kode yang ditulis (self-review) untuk kualitas & keamanan. |

---

## B. 🛠️ Kemampuan Tools (Tool Capabilities)

| No | Fitur | Deskripsi |
|----|-------|-----------|
| 6 | **Tool: `edit_file` (Patch)** | Edit bagian file tertentu tanpa menimpa seluruh file — hemat token dan lebih presisi dibanding `write_file` utuh. |
| 7 | **Tool: `search_code` (Grepping)** | Cari simbol, fungsi, atau pattern di seluruh project dengan cepat (mirip grep) tanpa membaca file satu per satu. |
| 8 | **Tool: `git_status` / `git_diff`** | Menjalankan perintah git untuk melihat perubahan, status branch, dan diff sebelum/sesudah modifikasi. |
| 9 | **Tool: `run_tests`** | Menjalankan test suite (pytest/vitest) otomatis dan membaca hasilnya untuk memverifikasi kode. |
| 10 | **Tool: `web_search`** | Mencari dokumentasi terbaru/library yang relevan ketika menghadapi API yang belum dikenal. |
| 11 | **Tool: `run_parallel`** | Mengeksekusi beberapa tool independen secara paralel untuk mempercepat penyelesaian tugas. |

---

## C. 🧠 Proaktif & Kontekstual (Proactive & Contextual)

| No | Fitur | Deskripsi |
|----|-------|-----------|
| 12 | **Auto-Sync Konteks** | Otomatis memperbarui `.orion/` (master_context, decision_log, current_sprint) setelah tugas besar tanpa harus diminta. |
| 13 | **Context Awareness (Lint & Error Parsing)** | Otomatis membaca error terminal/lint, mengerti masalahnya, lalu memperbaikinya tanpa diminta. |
| 14 | **Predictive Next Steps** | Setelah selesai satu tugas, menyarankan tugas berikutnya yang paling relevan berdasarkan roadmap project. |

---

## D. 🖥️ UI & Integrasi Extension

| No | Fitur | Deskripsi |
|----|-------|-----------|
| 15 | **Rich Rendering Chat** | Markdown, code block dengan syntax highlighting, dan tombol "Apply" untuk langsung menerapkan kode yang disarankan. |
| 16 | **Inline Editing & Diff Preview** | Menampilkan preview diff sebelum menerapkan perubahan ke file, sehingga user bisa menyetujui/menolak. |
| 17 | **Multi-Turn Task / Queue** | Mendukung antrian beberapa task yang dikerjakan berurutan dalam satu sesi. |
| 18 | **Progress & Status Panel** | Menampilkan status agentic loop (tool apa yang sedang dijalankan, iterasi ke berapa) secara visual. |
| 19 | **Quick Context Picker** | User bisa memilih file/folder tertentu untuk dijadikan fokus konteks sebelum bertanya. |

---

## E. ⚙️ Kualitas & Keandalan (Quality & Reliability)

| No | Fitur | Deskripsi |
|----|-------|-----------|
| 20 | **Test Automation (Self-Test)** | Membuat & menjalankan unit test untuk backend (`tool_executor`, `context_manager`) dan extension. |
| 21 | **Error Recovery & Retry** | Jika tool gagal (timeout/network), otomatis mencoba ulang dengan strategi berbeda. |
| 22 | **Configurable Agentic Loop** | Batas iterasi (15) bisa diatur per-task, tidak hardcoded, agar tugas kompleks tidak terhenti prematur. |
| 23 | **Sandbox Mode (Dry-run)** | Mode aman di mana menampilkan rencana aksi sebelum benar-benar mengubah file/terminal. |
| 24 | **Token Budget Management** | Manajemen penggunaan token agar tidak boros dan respons tetap hemat biaya. |

---

## F. 💬 Interaksi & User Experience

| No | Fitur | Deskripsi |
|----|-------|-----------|
| 25 | **Multi-Bahasa** | Mendukung respons dalam banyak bahasa otomatis mengikuti bahasa user. |
| 26 | **Memory Pribadi User** | Mengingat preferensi user (mis. "selalu gunakan type hints", "jangan ubah file X") lintas sesi. |
| 27 | **Command Palette & Shortcut** | Akses cepat ke fitur Orion lewat VS Code Command Palette / keyboard shortcut. |

---

## 📅 Rencana Pembagian Sprint (Proposal)

### Sprint Awal (Prioritas Tinggi)
- **#1** Streaming Respons (SSE)
- **#6** Tool `edit_file` (Patch)
- **#7** Tool `search_code` (Grepping)

### Sprint Berikutnya (Kualitas & Konteks)
- **#12** Auto-Sync Konteks
- **#20** Test Automation / Self-Test
- **#5** Self-Reflection / Code Review

### Sprint Lanjutan (Maturasi)
- **#2** Context Window Cerdas
- **#3** Multi-Session Memory
- **#16** Inline Editing & Diff Preview
- **#4** Autonomous Task Planning

### Backlog (Pengayaan)
- Sisanya (#8, #9, #10, #11, #13, #14, #15, #17, #18, #19, #21, #22, #23, #24, #25, #26, #27)

---

*Dokumen ini bisa diperbarui seiring progres pengerjaan fitur-fitur di atas.*

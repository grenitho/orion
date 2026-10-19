# Project Orion - Milestone: Agentic Loop Berhasil (v0.1)

## 1. Pencapaian
Backend Python berhasil mengimplementasikan Agentic Loop menggunakan Function Calling (Tool Use). AI kini dapat:
- Memutuskan sendiri kapan harus memanggil tools (`read_file`, `write_file`, `run_terminal`).
- Menerima hasil eksekusi tool dan menggunakannya untuk langkah selanjutnya.
- Memberikan respons teks final setelah tugas selesai.

## 2. Catatan Teknis
- Model yang digunakan saat ini: `gemini-3.5-flash`.
- Library `litellm` harus di-update ke versi terbaru agar mendukung parameter `tools` untuk model Gemini.
- Urutan pesan (`messages`) saat mengirim hasil tool ke API harus ketat: `role: assistant` (dengan tool_calls) harus selalu didahului oleh `role: tool` (hasil eksekusi).
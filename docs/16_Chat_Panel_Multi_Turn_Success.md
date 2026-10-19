# Project Orion - Fase 2: Chat Panel Multi-Turn & Tool Calling (v0.1)

## 1. Pencapaian
- Chat Panel berhasil menangani percakapan multi-putaran (multi-turn) dengan tool calling (`ask_user`).
- UI menggunakan `<textarea>` dengan dukungan `Shift+Enter` untuk input terstruktur.
- Activity Panel di sidebar menampilkan status real-time ("Mengirim pesan...", "Menerima balasan").

## 2. Masalah yang Ditemukan
- Jawaban AI terpotong saat respons terlalu panjang (masalah `max_tokens`).
- Perlu peningkatan limit token dan implementasi streaming untuk pengalaman chat yang lebih natural.
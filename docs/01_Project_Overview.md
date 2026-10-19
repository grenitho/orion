# Project Orion - Master Plan & Overview (v0.1)

## 1. Visi & Tujuan
**Project Orion** adalah asisten pemrograman AI otonom (Agentic AI) yang terintegrasi langsung ke dalam Visual Studio Code. 

Berbeda dengan chatbot AI konvensional yang hanya memberikan saran teks, Orion dirancang untuk **benar-benar memahami, menjelajahi, dan memanipulasi** kode sumber proyek secara langsung. Tujuannya adalah mengubah cara developer bekerja: dari yang sebelumnya "mengetik kode dan bertanya pada AI", menjadi "memberikan instruksi tingkat tinggi dan membiarkan AI mengeksekusinya".

Target awal proyek ini adalah untuk **penggunaan pribadi (internal tools)** guna mempercepat proses coding, menjaga kerapian arsitektur, dan mengotomatisasi tugas-tugas repetitif. Setelah stabil, Orion akan dibuka untuk publik.

---

## 2. Masalah yang Ingin Diselesaikan
Penggunaan AI untuk coding saat ini masih memiliki friksi (gesekan) yang besar:
* **Konteks yang Terputus:** Developer harus menjelaskan ulang struktur proyek atau menyalin-tempel (copy-paste) kode setiap kali memulai chat baru.
* **Kebutaan Konteks:** AI sering memberikan kode yang "bertabrakan" atau tidak sesuai dengan pola (pattern) proyek karena tidak tahu hubungan antar file.
* **Verifikasi Manual:** Developer harus menyalin kode dari AI, menempelkannya ke file, lalu menjalankan proyek secara manual untuk mengecek error, dan mengulang siklus ini jika ada bug.

Orion hadir untuk menghapus friksi tersebut dengan memberikan AI "tangan" dan "mata" untuk melihat dan mengedit proyek secara langsung.

---

## 3. Target Utama (Core Capabilities)
Ketika sebuah folder proyek dibuka di VS Code, Orion harus mampu:
1. **Memahami Konteks:** Membaca struktur folder, mengekstrak hubungan antar file (import/dependency), dan mengingat aturan coding proyek.
2. **Bertindak Otonom (Agentic):** Membuat file baru, mengedit file yang ada, dan melakukan refactoring tanpa perlu diinstruksikan baris demi baris.
3. **Mengeksekusi & Memverifikasi:** Menjalankan perintah terminal (seperti `npm install`, `pytest`), menjalankan aplikasi, dan membaca output error untuk diperbaiki secara mandiri.
4. **Persisten:** Menyimpan memori proyek (keputusan arsitektur, TODO, aturan) sehingga pengetahuan tidak hilang saat sesi chat berakhir.

---

## 4. Fitur Utama (Scope Versi 0.1)

### A. Multi-Model AI dengan Auto-Fallback
Pengguna tidak dikunci pada satu provider. Orion mendukung Gemini, Groq, OpenRouter, dan OpenAI. 
*Fitur Kunci:* **Auto-Fallback**. Jika satu model mencapai batas limit (rate limit), Orion akan otomatis beralih ke model cadangan dengan konteks yang sama persis, sehingga pekerjaan tidak pernah terhenti.

### B. Pemahaman Proyek Berbasis "Peta & Wilayah"
Alih-alih membakar token dengan membaca seluruh isi file secara mentah, Orion membuat "Peta" (struktur folder dan daftar import). AI hanya akan meminta untuk membaca "Wilayah" (isi penuh file) jika file tersebut benar-benar relevan dengan tugas yang sedang dikerjakan.

### C. Eksekusi Langsung & Terminal Integration
AI dapat menjalankan perintah shell. Untuk perintah yang berisiko (seperti instalasi package atau penghapusan file), Orion akan meminta persetujuan eksplisit dari pengguna (Human-in-the-loop).

### D. UI yang Transparan (Activity Panel)
Proses AI tidak disembunyikan. Panel khusus di VS Code akan menampilkan status real-time (misal: *🟢 Membaca file...*, *🔴 Menunggu persetujuan terminal...*) agar pengguna selalu tahu apa yang sedang dilakukan AI dan tidak mengira aplikasi sedang *freeze*.

---

## 5. Alur Kerja Sistem (The Agentic Loop)
Secara sederhana, siklus kerja Orion adalah:
1. **Ingest:** Pengguna membuka proyek -> Orion memindai dan membuat "Peta Proyek".
2. **Prompt:** Pengguna memberikan instruksi tingkat tinggi (misal: *"Buatkan fitur Login Google"*).
3. **Plan & Act:** AI menganalisis peta, meminta akses ke file spesifik, menulis kode, dan menginstal dependensi.
4. **Verify:** Orion menjalankan terminal untuk mengecek apakah kode berjalan.
5. **Self-Correction:** Jika ada error di terminal, Orion menangkap error tersebut dan memperbaikinya sendiri secara otomatis.
6. **Complete:** AI melaporkan tugas selesai kepada pengguna.

---

## 6. Prinsip Pengembangan
1. **Fokus pada MVP:** Mulai dari fitur yang paling penting. Jangan menambahkan fitur kompleks (seperti Multi-Agent atau Cloud Sync) sebelum versi dasar stabil.
2. **Transparansi Penuh:** AI hanya membantu, keputusan akhir (terutama untuk eksekusi file dan terminal) tetap berada di tangan pengguna.
3. **Dokumentasi adalah Kunci:** Semua perubahan dicatat di Git, dan semua keputusan arsitektur didokumentasikan di folder `docs/`.
4. **Efisiensi Token:** Selalu utamakan cara yang paling hemat token (menggunakan struktur folder dan ringkasan) sebelum mengirim isi file penuh ke AI.

---

## 7. Visi Jangka Panjang
Setelah v0.1 stabil dan digunakan sehari-hari, Orion akan berevolusi menjadi **AI Software Engineer** penuh. Ia tidak hanya menulis kode, tetapi juga mampu merancang arsitektur sistem dari nol, menulis unit test secara otomatis, melakukan code review, dan menjaga dokumentasi proyek agar selalu mutakhir (self-updating documentation).
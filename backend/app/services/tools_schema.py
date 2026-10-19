"""
tools_schema.py - skema 22 tool Orion (Fase A pemecahan main.py)
Data murni: tanpa logika, tanpa impor berat.
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "Membaca struktur folder dan file di path tertentu.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path relatif folder (default: '.').",
                    },
                    "depth": {
                        "type": "integer",
                        "description": "Kedalaman folder (default: 2).",
                    },
                },
                # TIDAK ADA "required": [] DI SINI
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Membaca isi lengkap dari sebuah file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path relatif file dari root project.",
                    }
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Membuat file baru atau menimpa file yang ada.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path relatif file.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Konten lengkap yang akan ditulis.",
                    },
                },
                "required": ["file_path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Mengedit bagian spesifik dari file yang sudah ada menggunakan format SEARCH/REPLACE.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path relatif file.",
                    },
                    "diff": {
                        "type": "string",
                        "description": "Blok pencarian dan penggantian. Format: <<<<<<< SEARCH\\n[kode lama]\\n=======\\n[kode baru]\\n>>>>>>> REPLACE",
                    },
                },
                "required": ["file_path", "diff"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": "Mencari string atau regex di seluruh project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Kata kunci atau regex yang ingin dicari.",
                    },
                    "file_pattern": {
                        "type": "string",
                        "description": "Filter ekstensi file (misal: '*.py'). Kosongkan untuk semua file.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "restore_backup",
            "description": "Pulihkan sebuah file dari backup terakhirnya di .orion/backups. Reversibel: versi sekarang disimpan dulu sebagai .pre-restore.bak. Pakai ini kalau edit_file/write_file ternyata merusak sesuatu.",
            "parameters": {"type": "object", "properties": {"file_path": {"type": "string", "description": "Path relatif file yang akan dipulihkan."}}, "required": ["file_path"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_backups",
            "description": "Daftar backup yang tersedia untuk dipulihkan. Pakai sebelum restore_backup kalau tidak yakin backupnya ada.",
            "parameters": {"type": "object", "properties": {"file_path": {"type": "string", "description": "Opsional: saring berdasarkan path."}}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "Hapus sebuah file SECARA AMAN: salinan terakhirnya di-backup dulu ke .orion/backups sehingga bisa dipulihkan. JANGAN menghapus file lewat run_terminal.",
            "parameters": {"type": "object", "properties": {"file_path": {"type": "string", "description": "Path relatif file yang akan dihapus."}}, "required": ["file_path"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_file",
            "description": "Pindah atau ganti nama sebuah file di dalam project, dengan backup sumber dulu. Menolak menimpa file yang sudah ada di tujuan.",
            "parameters": {"type": "object", "properties": {"from_path": {"type": "string"}, "to_path": {"type": "string"}}, "required": ["from_path", "to_path"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_commit",
            "description": "Buat titik aman (checkpoint) dengan git add -A + git commit. Pakai SETELAH sekelompok perubahan lolos verify_code, supaya ada titik kembali yang bersih. Menolak dengan penjelasan kalau folder bukan repo git. JANGAN commit lewat run_terminal.",
            "parameters": {"type": "object", "properties": {"message": {"type": "string", "description": "Pesan commit yang menjelaskan perubahan."}}, "required": ["message"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": (
                "Menjalankan test suite project (pytest / npm test / go test, terdeteksi "
                "otomatis) dan mengembalikan laporan TERURAI: jumlah lulus/gagal plus daftar "
                "test yang gagal dan satu baris penyebabnya. WAJIB dipanggil setelah mengubah "
                "kode bila project punya test, dan WAJIB dikutip hasilnya saat melaporkan "
                "selesai. Selama test masih merah, klaim selesai adalah kebohongan."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Opsional: perintah test manual. Kosongkan untuk deteksi otomatis."},
                    "timeout": {"type": "integer", "description": "Batas detik (default 180, maks 600).", "default": 180},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "verify_code",
            "description": (
                "Memeriksa file APA PUN dengan parser/compiler sungguhan: .py (sintaks + "
                "impor yang tidak terpasang + ruff), .js/.mjs/.cjs/.jsx (node --check), "
                ".ts/.tsx (tsc --noEmit), .html/.htm (sintaks script inline, id duplikat, "
                "referensi DOM, keseimbangan tag), .json/.xml/.svg/.yaml/.css. Menerima "
                "juga FOLDER untuk memeriksa banyak file sekaligus. WAJIB dipanggil "
                "SEBELUM menyatakan kode berfungsi, lulus, atau selesai. Model tidak "
                "bisa melihat kurung yang hilang; pemeriksa ini bisa."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path relatif file atau folder yang akan diperiksa.",
                    },
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "verify_html",
            "description": (
                "Memeriksa file HTML/JS dengan pemeriksa NYATA: node --check untuk sintaks "
                "JavaScript, deteksi id duplikat, dan referensi getElementById/querySelector "
                "yang elemennya tidak ada. WAJIB dipanggil SEBELUM menyatakan sebuah halaman "
                "berfungsi, terhubung, atau bebas error. Satu kurung yang hilang mematikan "
                "seluruh blok script, dan hal semacam itu TIDAK terlihat dengan membaca kode."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path relatif file .html/.htm/.js yang akan diperiksa.",
                    },
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": (
                "Membaca ISI sebuah URL secara utuh dan mengembalikannya sebagai teks. "
                "Gunakan SETELAH web_search menemukan URL yang relevan, atau untuk membaca "
                "dokumentasi resmi / halaman issue / changelog. web_search hanya memberi "
                "cuplikan; tool ini memberi isi lengkapnya."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL lengkap yang ingin dibaca (http/https).",
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "Batas karakter isi yang dikembalikan (default 20000, maksimum 100000).",
                        "default": 20000,
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Mencari informasi real-time dari internet. WAJIB digunakan jika mengalami error yang tidak dikenal, butuh URL terbaru, atau dokumentasi API.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Kata kunci pencarian yang spesifik (misal: 'piper tts indonesian male voice huggingface url').",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Jumlah hasil maksimal (default 5).",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file_range",
            "description": "Membaca file per rentang baris.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path relatif file.",
                    },
                    "start_line": {"type": "integer", "description": "Baris awal."},
                    "end_line": {"type": "integer", "description": "Baris akhir."},
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_json_path",
            "description": "Membaca nilai spesifik dari file JSON menggunakan dot notation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path relatif file JSON.",
                    },
                    "json_path": {
                        "type": "string",
                        "description": "Path key dengan pemisah titik.",
                    },
                },
                "required": ["file_path", "json_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_terminal",
            "description": "Menjalankan perintah terminal/shell.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Perintah terminal."}
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": "HENTIKAN proses untuk berdiskusi dengan user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "Pertanyaan atau penawaran opsi kepada user.",
                    }
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_todos",
            "description": (
                "Buat atau perbarui daftar todo (checklist) untuk task multi-langkah "
                "yang sedang dikerjakan, supaya progres terlihat jelas oleh user. "
                "WAJIB dipanggil di AWAL saat menerima instruksi berpoin/multi-langkah "
                "(misal 'tambahkan fitur A, B, C'), lalu update status tiap item "
                "seiring progres (pending -> in_progress -> completed). "
                "Untuk task sederhana satu langkah, tool ini BOLEH dilewati."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "todos": {
                        "type": "array",
                        "description": "Daftar lengkap todo (mengganti seluruh list sebelumnya).",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {
                                    "type": "string",
                                    "description": "ID unik singkat, misal '1', '2'.",
                                },
                                "text": {
                                    "type": "string",
                                    "description": "Deskripsi singkat sub-task.",
                                },
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "in_progress", "completed"],
                                    "description": "Status terkini sub-task ini.",
                                },
                            },
                            "required": ["text"],
                        },
                    }
                },
                "required": ["todos"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "Melihat daftar singkat file yang telah dimodifikasi, ditambah, atau dihapus.",
            "parameters": {
                "type": "object"
                # HANYA "type": "object". TIDAK ADA properties atau required.
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": "Melihat perubahan kode.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path relatif file spesifik (opsional). Kosongkan untuk ringkasan.",
                    }
                },
                # TIDAK ADA "required": [] DI SINI
            },
        },
    },
]

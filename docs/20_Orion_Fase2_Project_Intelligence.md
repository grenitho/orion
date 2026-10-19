# 20 - Orion Fase 2: Project Intelligence (Dependency Map & AST)

## 1. Tujuan Fase Ini
Mengubah Orion dari "Junior Dev yang baca file satu per satu" menjadi "Senior Dev yang tahu arsitektur project di luar kepala".

**Masalah Saat Ini:**
- Orion hanya tahu **nama file** (dari `list_files`), tapi tidak tahu **isi dan hubungan** antar file.
- Jika user bilang "ubah class `UserService`", Orion harus `search_code` dulu, lalu `read_file` satu per satu untuk menemukan semua file yang terpengaruh.
- Ini boros token, lambat, dan rawan miss (Orion bisa lupa mengupdate 1 file yang mengimport `UserService`).
- User masih harus membuat dokumentasi manual di `.orion/` agar Orion "paham" konteks project.

**Tujuan Akhir:**
- Backend otomatis memindai seluruh project dan menghasilkan `dependency_map.json`.
- Orion langsung tahu: "File A mengimport Class X dari File B" tanpa membaca isi file.
- Dokumentasi manual di `.orion/` menjadi **opsional**, bukan wajib.

---

## 2. Arsitektur Dependency Map

### 2.1 Struktur `dependency_map.json`
File ini akan di-generate otomatis di folder `.orion/` oleh backend.

```json
{
  "generated_at": "2025-01-15T10:30:00",
  "total_files": 42,
  "files": {
    "backend/app/main.py": {
      "language": "python",
      "imports": [
        {"module": "fastapi", "names": ["FastAPI"]},
        {"module": "app.services.tool_executor", "names": ["ToolExecutor"]},
        {"module": "app.services.context_manager", "names": ["ContextManager"]}
      ],
      "exports": {
        "classes": [],
        "functions": ["root", "set_project", "chat_with_orion", "clear_chat"],
        "variables": ["app", "context_manager", "tool_executor"]
      }
    },
    "backend/app/services/tool_executor.py": {
      "language": "python",
      "imports": [
        {"module": "re", "names": ["*"]},
        {"module": "os", "names": ["*"]},
        {"module": "subprocess", "names": ["*"]}
      ],
      "exports": {
        "classes": ["ToolExecutor"],
        "functions": [],
        "variables": []
      }
    },
    "src/extension.ts": {
      "language": "typescript",
      "imports": [
        {"module": "vscode", "names": ["*"]},
        {"module": "./backendManager", "names": ["BackendManager"]}
      ],
      "exports": {
        "classes": [],
        "functions": ["activate", "deactivate"],
        "variables": []
      }
    }
  },
  "reverse_map": {
    "ToolExecutor": ["backend/app/main.py"],
    "ContextManager": ["backend/app/main.py"],
    "BackendManager": ["src/extension.ts"]
  }
}
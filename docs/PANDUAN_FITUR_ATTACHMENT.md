# Panduan Memasang Fitur Lampiran Gambar & File di ORION

**Status kode:** `attachments.py` selesai dan **14/14 test lulus** (pytest, tanpa API key).
**Perubahan arsitektur:** tidak ada — tetap FastAPI + SSE + TS extension.

---

## Ringkasan: 5 perubahan di backend, 4 di extension

| # | File | Perubahan | Risiko |
|---|---|---|---|
| B1 | `backend/app/services/attachments.py` | **FILE BARU** — salin apa adanya | Tidak ada |
| B2 | `backend/app/services/test_attachments.py` | **FILE BARU** — test | Tidak ada |
| B3 | `main.py` — `class ChatRequest` | tambah 1 field | Rendah |
| B4 | `main.py` — awal `/chat` | proses lampiran | Rendah |
| B5 | `main.py` — `estimate_message_tokens` | tahan content berupa list | Rendah |
| E1–E4 | `orion/src/panels/ChatPanel.ts` | tombol lampir + kirim base64 | Sedang |

**Urutan pemasangan:** B1 → B2 → jalankan test → B3 → B4 → B5 → uji backend → E1–E4 → uji extension.
Jangan sekaligus; kalau ada yang rusak, Anda tahu di langkah mana.

---

## B1 & B2 — salin dua file baru

```powershell
Copy-Item "D:\orion-fitur\backend\app\services\attachments.py"      "D:\APP Grenitho\orion-project\backend\app\services\" -Force
Copy-Item "D:\orion-fitur\backend\app\services\test_attachments.py" "D:\APP Grenitho\orion-project\backend\app\services\" -Force
```

Jalankan test-nya (ini sekaligus test pertama ORION):

```powershell
cd "D:\APP Grenitho\orion-project"
.venv\Scripts\Activate.ps1
pip install pytest
python -m pytest backend\app\services\test_attachments.py -v
```

Harus muncul **`14 passed`**. Kalau belum hijau, jangan lanjut — berarti ada yang salah salin.

> Salah satu test itu (`test_model_diberitahu_kalau_lampiran_gagal`) menemukan bug nyata
> saat saya menulis modulnya: `base64.b64decode("!!!")` **tidak** melempar error, ia
> mengembalikan byte kosong, sehingga lampiran rusak lolos sebagai "gambar 0 KB" dan
> dikirim ke model. Sudah diperbaiki. Ini contoh konkret kenapa rasio test 0% berbahaya.

---

## B3 — `ChatRequest`

**Cari** (sekitar baris 693):
```python
class ChatRequest(BaseModel):
    message: str
```

**Ganti jadi:**
```python
class ChatRequest(BaseModel):
    message: str
    # >>> FITUR LAMPIRAN: daftar {name, mime, data(base64)} dari extension.
    # Default [] supaya request lama (tanpa field ini) tetap valid.
    attachments: list = []
```

---

## B4 — awal `/chat`

**Cari** (sekitar baris 859):
```python
    context_manager.add_message("user", request.message)
```

**Ganti jadi:**
```python
    # >>> FITUR LAMPIRAN ==============================================
    # Gambar TIDAK disimpan ke riwayat. Base64 satu screenshot 1 MB jadi
    # ±1,4 juta karakter; kalau masuk session_history.json maka setiap
    # iterasi agent mengirim ulangnya dan kuota free tier habis cepat.
    # Riwayat hanya menyimpan placeholder teks; gambar asli disuntikkan
    # ke api_messages HANYA pada iterasi pertama (lihat patch di bawah).
    # ==================================================================
    from app.services.attachments import build_user_content, describe_attachments

    _lamp_desc = describe_attachments(request.attachments)
    _content_parts, _riwayat_text = build_user_content(request.message, request.attachments)
    _pending_image_parts = _content_parts if isinstance(_content_parts, list) else None

    if _lamp_desc:
        logger.info("[ATTACHMENT] %s", _lamp_desc)
        yield_placeholder = _lamp_desc   # dipakai di event status di bawah
    else:
        yield_placeholder = None

    context_manager.add_message("user", _riwayat_text)
```

Lalu **cari baris** `@app.post("/chat")` → di dalam `generate_stream()`, tepat setelah
`yield f"data: {json.dumps({'type': 'status', ...})}"` pertama (atau di awal generator),
tambahkan:

```python
        if yield_placeholder:
            yield f"data: {json.dumps({'type': 'status', 'text': f'📎 {yield_placeholder}'})}\n\n"
```

**Dan di dalam loop**, cari (sekitar baris 1145):
```python
                # >>> LANGKAH 3: SHORT REPLY ANTI-AMNESIA GUARD <<<
                if _ == 0:
```

**Sisipkan SEBELUM baris itu:**
```python
                # >>> FITUR LAMPIRAN: suntik gambar hanya di iterasi pertama.
                # Setelah itu riwayat sudah berisi placeholder teks, jadi
                # iterasi berikutnya tidak mengirim ulang base64.
                if _ == 0 and _pending_image_parts:
                    for _i in range(len(api_messages) - 1, -1, -1):
                        if api_messages[_i].get("role") == "user":
                            api_messages[_i] = {
                                **api_messages[_i],
                                "content": _pending_image_parts,
                            }
                            break
                    _pending_image_parts = None   # sekali pakai
                # >>> AKHIR FITUR LAMPIRAN
```

---

## B5 — `estimate_message_tokens`

**Cari** (sekitar baris 105):
```python
    total = estimate_tokens(str(msg.get("content") or ""))
```

**Ganti jadi:**
```python
    # >>> FITUR LAMPIRAN: content bisa berupa LIST part (multimodal).
    # Tanpa ini, str(list) menghasilkan repr Python yang memuat seluruh
    # base64 -> estimasi token meledak dan auto-summarize salah potong.
    _c = msg.get("content")
    if isinstance(_c, list):
        _teks = " ".join(
            str(p.get("text", "")) for p in _c
            if isinstance(p, dict) and p.get("type") == "text"
        )
        # gambar dihitung tetap: Gemini menagih ±258 token per gambar
        _n_img = sum(1 for p in _c if isinstance(p, dict) and p.get("type") == "image_url")
        total = estimate_tokens(_teks) + (_n_img * 258)
    else:
        total = estimate_tokens(str(_c or ""))
```

**Uji backend sebelum lanjut:**
```powershell
cd "D:\APP Grenitho\orion-project\backend"
python -c "from app.main import app; print('OK, routes:', len(app.routes))"
```

---

## E1–E4 — `orion/src/panels/ChatPanel.ts`

### E1 · Tambahkan state & tombol di HTML webview

Cari baris yang membuat `input` dan `btn` (sekitar baris 190–200), lalu **tambahkan sebelum `<input id="chatInput"`**:

```html
<button id="attachBtn" title="Lampirkan gambar atau file"
        style="background:none;border:none;cursor:pointer;font-size:16px;padding:4px 6px;">📎</button>
<input type="file" id="fileInput" multiple style="display:none"
       accept=".png,.jpg,.jpeg,.webp,.heic,.heif,.py,.ts,.js,.json,.md,.txt,.log,.yaml,.yml,.css,.html,.sql,.sh">
<div id="attachList" style="display:flex;flex-wrap:wrap;gap:4px;margin:4px 0;"></div>
```

### E2 · Logika membaca file jadi base64

Sisipkan di dalam skrip webview, **sebelum** `window.addEventListener('message', ...)`:

```javascript
// >>> FITUR LAMPIRAN =========================================
const MAX_GAMBAR = 4;
const MAX_MB_GAMBAR = 7;          // batas inline Gemini
const MIME_GAMBAR = ['image/png','image/jpeg','image/jpg','image/webp','image/heic','image/heif'];
let attachments = [];

function renderAttachList() {
    const box = document.getElementById('attachList');
    box.innerHTML = '';
    attachments.forEach((a, i) => {
        const chip = document.createElement('span');
        chip.style.cssText = 'font-size:11px;padding:2px 6px;border-radius:10px;background:rgba(255,255,255,.12);';
        chip.textContent = (a.mime.startsWith('image/') ? '🖼 ' : '📄 ') + a.name + ' ✕';
        chip.style.cursor = 'pointer';
        chip.onclick = () => { attachments.splice(i, 1); renderAttachList(); };
        box.appendChild(chip);
    });
}

function tambahFile(file) {
    const isGambar = MIME_GAMBAR.includes(file.type) ||
                     /\.(png|jpe?g|webp|heic|heif)$/i.test(file.name);
    if (isGambar && file.size > MAX_MB_GAMBAR * 1024 * 1024) {
        appendMsg(`⚠️ **${file.name}** lebih dari ${MAX_MB_GAMBAR} MB (batas Gemini). Ditolak.`, 'ai');
        return;
    }
    if (isGambar && attachments.filter(a => a.mime.startsWith('image/')).length >= MAX_GAMBAR) {
        appendMsg(`⚠️ Maksimal ${MAX_GAMBAR} gambar per pesan.`, 'ai');
        return;
    }
    if (file.size > 2 * 1024 * 1024 && !isGambar) {
        appendMsg(`⚠️ **${file.name}** terlalu besar (${(file.size/1048576).toFixed(1)} MB). Maksimal 2 MB untuk file teks.`, 'ai');
        return;
    }
    const reader = new FileReader();
    reader.onload = () => {
        const dataUri = String(reader.result);          // "data:<mime>;base64,...."
        attachments.push({
            name: file.name,
            mime: file.type || (isGambar ? 'image/png' : 'text/plain'),
            data: dataUri.split(',')[1] || dataUri
        });
        renderAttachList();
    };
    reader.onerror = () => appendMsg(`⚠️ Gagal membaca **${file.name}**.`, 'ai');
    reader.readAsDataURL(file);
}

document.getElementById('attachBtn').addEventListener('click', () =>
    document.getElementById('fileInput').click());

document.getElementById('fileInput').addEventListener('change', e => {
    Array.from(e.target.files || []).forEach(tambahFile);
    e.target.value = '';                                 // reset supaya bisa pilih ulang
});

// drag & drop ke panel chat
document.body.addEventListener('dragover', e => { e.preventDefault(); });
document.body.addEventListener('drop', e => {
    e.preventDefault();
    Array.from(e.dataTransfer?.files || []).forEach(tambahFile);
});

// paste gambar dari clipboard (screenshot dengan Win+Shift+S lalu Ctrl+V)
document.addEventListener('paste', e => {
    const items = Array.from(e.clipboardData?.items || []);
    for (const it of items) {
        if (it.kind === 'file') {
            const f = it.getAsFile();
            if (f) { tambahFile(f); e.preventDefault(); }
        }
    }
});
// >>> AKHIR FITUR LAMPIRAN ================================
```

### E3 · Kirim lampiran di fungsi `send()`

Cari `body: JSON.stringify({ message: ... })` di dalam `send()`, **ganti jadi**:

```javascript
body: JSON.stringify({ message: text, attachments: attachments })
```

Lalu **setelah** fetch dimulai, bersihkan daftar:

```javascript
attachments = [];
renderAttachList();
```

Dan tampilkan lampiran di gelembung pesan user (opsional tapi enak dilihat):

```javascript
appendMsg(text + (attachments.length ? `\n\n📎 ${attachments.map(a => a.name).join(', ')}` : ''), 'user');
```

> ⚠️ Urutan penting: ambil `attachments` **sebelum** dikosongkan. Baca dulu nilainya ke
> variabel lokal kalau perlu: `const kirim = attachments; attachments = [];`

### E4 · Build ulang

```powershell
cd "D:\APP Grenitho\orion-project\orion"
npm run compile
```

Lalu di VS Code: `Ctrl+Shift+P` → **Reload Window**.

---

## Cara menguji

1. **Screenshot error** dengan `Win+Shift+S`, lalu di panel Orion tekan `Ctrl+V` → chip `🖼 image.png` muncul
2. Ketik: *"Perbaiki error di screenshot ini"* → Kirim
3. Perhatikan: muncul status `📎 1 lampiran: image.png`, dan Orion menyebut isi gambar itu

Uji juga jalur gagal:
- Lampirkan file `.zip` → backend menolak, dan **model diberi tahu** bahwa ia tidak bisa melihatnya (supaya tidak mengarang)
- Lampirkan gambar 10 MB → ditolak di sisi extension sebelum terkirim

---

## Yang perlu diwaspadai

| Hal | Catatan |
|---|---|
| **Kuota free tier** | Gambar menambah token. Gemini menagih ±258 token per gambar, jauh lebih murah daripada base64-nya — tapi tetap pantau `.orion/performance_log.jsonl` |
| **`thought_signature`** | Kalau nanti Anda menyalakan streaming/tool call dengan gambar, pastikan litellm ≥ 1.105.1 (sudah Anda punya) |
| **Ukuran request** | 4 gambar × 7 MB = 28 MB body request. Uvicorn menampungnya, tapi kalau lambat, turunkan `MAX_GAMBAR` |
| **Model fallback** | OpenRouter/Cloudflare tier 2–3 mungkin tidak mendukung format gambar yang sama. Kalau Gemini gagal dan jatuh ke fallback, gambar bisa diabaikan — `attachments.py` sudah mengirim placeholder teks ke riwayat, jadi minimal Orion tahu ada lampiran |

---

## Kalau Anda ingin saya menerapkannya langsung

Saya punya repo Anda di workspace. Kalau Anda mau, saya bisa menerapkan B3–B5 ke `main.py`
dan E1–E4 ke `ChatPanel.ts` secara langsung (seperti 18 patch sebelumnya), memverifikasi dengan
`py_compile` + `tsc --noEmit`, lalu mengemas hasilnya jadi ZIP. Tinggal bilang.

Yang **tidak** bisa saya verifikasi tanpa mesin Anda: apakah webview benar-benar merender
tombolnya dan apakah Gemini menerima gambarnya. Itu butuh API key + VS Code.

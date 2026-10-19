"""
sse.py — pembuat event SSE (Fase C langkah 1)
==============================================
Sebelum modul ini ada, 47 titik di dalam endpoint /chat merakit frame SSE
secara inline: f"data: {json.dumps({'type': ...})}\\n\\n". Format frame dan
tujuh nama event di dalamnya adalah KONTRAK BEKU dengan extension
(FASE_C_PLAN.md bagian 1) — ChatPanel._processStreamData menangani tepat
tujuh nama ini, dan jaminan "tepat satu done per run" dipegang oleh
stream_utils.jamin_done.

ev() menghasilkan frame yang IDENTIK-BYTE dengan bentuk inline lama
(kunci 'type' selalu pertama, json.dumps dengan default yang sama), jadi
pemindahannya murni perubahan bentuk panggilan tanpa perubahan perilaku.
Test-nya ada di test_sse.py; nama-nama beku dijaga juga oleh guard AST
di sana (tidak boleh ada frame rakitan manual yang kembali muncul).
"""

from __future__ import annotations

import json

# ============================================================
# KONTRAK BEKU — tujuh nama event (FASE_C_PLAN.md bagian 1).
# JANGAN rename/tambah/kurangi tanpa perubahan yang sengaja dan
# terpisah di orion/src/panels/ChatPanel.ts (_processStreamData).
# ============================================================
EVENT_STATUS = "status"                      # baris log aktivitas / progres
EVENT_TOKEN = "token"                        # teks jawaban ke bubble assistant
EVENT_ASK_USER = "ask_user"                  # berhenti dan tanya user
EVENT_CONFIRM_TERMINAL = "confirm_terminal"  # modal persetujuan perintah
EVENT_TODO_UPDATE = "todo_update"            # checklist todo
EVENT_ERROR = "error"                        # bubble error
EVENT_DONE = "done"                          # sinyal run berakhir normal

FROZEN_EVENTS = (
    EVENT_STATUS,
    EVENT_TOKEN,
    EVENT_ASK_USER,
    EVENT_CONFIRM_TERMINAL,
    EVENT_TODO_UPDATE,
    EVENT_ERROR,
    EVENT_DONE,
)


def ev(event_type: str, **payload) -> str:
    """Rakit satu frame SSE: 'data: {"type": <event_type>, ...payload}\\n\\n'.

    'type' selalu menjadi kunci PERTAMA — sama seperti bentuk inline lama,
    sehingga json.dumps (yang mempertahankan urutan insersi dict) menghasilkan
    byte yang identik. Urutan kwarg pemanggil menentukan urutan kunci payload
    berikutnya, persis seperti urutan kunci di literal dict yang digantikannya.
    """
    return f"data: {json.dumps({'type': event_type, **payload})}\n\n"

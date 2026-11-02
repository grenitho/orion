"""UPDATE-026 commit 34 — residual sweep (owner directive 2026-11-02:
inventarisir ulang semua bug, lalu perbaiki).

Three documented residuals, three code teeth (SYSTEM_PROMPT untouched):

1. handoff §6 residual 2 — a LEADING quote paragraph froze the planning
   stripper and let a one-line monologue survive as paragraph 2. The
   chain now skips ONE leading quote paragraph and strips the planning
   run behind it (quote + real answer kept; quote-only text untouched).
2. handoff §6 residual 5 (exam 6 shape) — the checkpoint file list is
   code-captured, but the model's OWN prose could still claim "changed
   `app/main.py`" on a zero-change turn: two contradictory lists in one
   message. On a zero-change turn, non-negated claim paragraphs naming
   a path are stripped; negated sentences and honest declines stay.
3. handoff §10 candidate — an order naming a browser/MCP tool the
   connected config lacks discovered the gap only at closing. A
   turn-start probe (once per turn) discloses the missing names in the
   status frame AND a context message so the first answer can decline
   the impossible part honestly.
"""

import pathlib

from app.services import guard_rules
from app.services import session_state
from app.services import stream_utils as su

_KUTIP_MONOLOG = (
    "> Saya Mau tampilan front end seperti petunjuk yang ada didalam "
    "file ini.\n\n"
    "Ah, read_file on App.jsx was truncated. Let's use search_code "
    "instead for the subtab block.\n\n"
    "Berikut hasil pemeriksaannya: subtab aktif memakai teks putih di "
    "latar #4f46e5."
)


def test_monolog_setelah_paragraf_kutipan_dibuang():
    bersih = su.bersih_closing(_KUTIP_MONOLOG)
    assert bersih.startswith("> Saya Mau tampilan")
    assert "Ah, read_file on App.jsx was truncated" not in bersih
    assert "Berikut hasil pemeriksaannya" in bersih


def test_kutipan_saja_tidak_berubah():
    teks = (
        "> kutipan user\n\n"
        "Let's check git_status first and then read the file."
    )
    assert su.bersih_closing(teks) == teks


def test_tanpa_kutipan_perilaku_lama_utuh():
    teks = "Jawaban langsung untuk Anda: tidak ada yang berubah."
    assert su.bersih_closing(teks) == teks


def test_klaim_file_dibuang_saat_nihil():
    teks = (
        "Saya mengubah `app/main.py` dan berhasil menulis "
        "`app/routers/x.py` sesuai permintaan.\n\n"
        "Kode pembacaan ada di `app/main.py` baris 12; tidak ada yang "
        "saya ubah turn ini."
    )
    bersih = su.buang_klaim_file_bila_nihil(teks, 0)
    assert "Saya mengubah `app/main.py`" not in bersih
    # paragraf negasi/jujur tetap hidup
    assert "tidak ada yang saya ubah turn ini" in bersih


def test_klaim_file_negasi_dan_penolakan_tetap():
    jujur = (
        "Tidak ada file yang berubah turn ini; saya hanya membaca "
        "`app/main.py`. No files changed — pemeriksaan saja."
    )
    assert su.buang_klaim_file_bila_nihil(jujur, 0) == jujur


def test_klaim_file_dibiarkan_saat_ada_perubahan():
    teks = "Saya mengubah `app/main.py`; files changed: `app/main.py`."
    assert su.buang_klaim_file_bila_nihil(teks, 1) == teks


def test_bersih_closing_menggabung_tiga_lapis():
    teks = (
        "[SISTEM - PEMERIKSAAN BUKTI]: Anda memakai kata-kata.\n\n"
        "Saya mengubah `app/main.py` turn ini.\n\n"
        "Fakta: kode ada di `app/main.py` baris 12, tanpa perubahan."
    )
    bersih = su.bersih_closing(teks, 0)
    assert "[SISTEM - PEMERIKSAAN BUKTI]" not in bersih
    assert "Saya mengubah `app/main.py` turn ini." not in bersih
    assert bersih.startswith("Fakta: kode ada di `app/main.py` baris 12")


def test_probe_tool_dinamai_tak_tersedia():
    tersedia = {"mcp__playwright__browser_navigate",
                "mcp__playwright__browser_evaluate"}
    order = ("verifikasi via Playwright MCP: browser_navigate lalu "
             "screenshot dengan browser_take_screenshot")
    hilang = guard_rules.tool_dinamai_tak_tersedia(order, tersedia)
    assert hilang == ["browser_take_screenshot"]
    # nama yang tersedia (suffix public name) tidak dilaporkan
    order2 = "pakai browser_evaluate untuk getComputedStyle"
    assert guard_rules.tool_dinamai_tak_tersedia(order2, tersedia) == []
    # tanpa nama tool -> kosong
    assert guard_rules.tool_dinamai_tak_tersedia("ubah warna tombol",
                                                 tersedia) == []
    # mcp mati total: semua nama browser dianggap hilang
    assert guard_rules.tool_dinamai_tak_tersedia(
        "browser_navigate ke localhost", set()) == ["browser_navigate"]


def test_state_probe_reset_per_turn():
    st = session_state.SessionState()
    st.probe_tool_hilang = 1
    st.reset_per_turn()
    assert st.probe_tool_hilang == 0


def test_wiring_probe_dan_arg_ledger():
    import re as _re
    src = (pathlib.Path(su.__file__).parent / "agent_loop.py").read_text(
        encoding="utf-8")
    assert "guard_rules.tool_dinamai_tak_tersedia(" in src
    assert "state.probe_tool_hilang" in src
    assert len(_re.findall(
        r"bersih_closing\(\s*strip_thinking_blocks\(content\),\s*"
        r"len\(state\.files_changed_this_turn\),", src)) == 2
    assert "TOOL AVAILABILITY" in src and "KETERSEDIAAN TOOL" in src

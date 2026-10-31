"""
test_crlf_edit_and_empty_terminal.py — UPDATE-025 commit 20
============================================================
Pins the two EXECUTION defects that cost Orion 80 iterations on the
InvoiceFlow exam (field transcript 2026-10-31):

1. edit_file matched model-authored LF SEARCH blocks against CRLF Windows
   sources verbatim, so every block "was not found" and the model rewrote
   whole files (or confabulated) instead of editing. The match must now
   fall back across line-ending styles while the file keeps its own.
2. run_terminal answered an empty stdout with "Success, no output." - a
   string that reads like health. Exit 0 + empty stdout must now name the
   mangled-quoting suspect instead of smiling.
"""

from __future__ import annotations

from app.services.tool_executor import ToolExecutor

DIFF_LF = (
    "<<<<<<< SEARCH\n"
    "warna = 'gelap'\n"
    "=======\n"
    "warna = 'terang'\n"
    ">>>>>>> REPLACE"
)

DIFF_CRLF = (
    "<<<<<<< SEARCH\r\n"
    "warna = 'gelap'\r\n"
    "=======\r\n"
    "warna = 'terang'\r\n"
    ">>>>>>> REPLACE"
)


def _ex(tmp_path) -> ToolExecutor:
    return ToolExecutor(str(tmp_path))


# ---------------------------------------------------- CRLF-tolerant editing


def test_edit_file_file_crlf_blok_lf_berhasil_dan_gaya_dipertahankan(tmp_path):
    ex = _ex(tmp_path)
    ex.write_file("app.jsx", "header\r\nwarna = 'gelap'\r\nfooter\r\n")
    hasil = ex.edit_file("app.jsx", DIFF_LF)
    assert hasil.startswith("Success: edited file")
    isi = open(tmp_path / "app.jsx", "rb").read().decode("utf-8")
    assert "warna = 'terang'" in isi
    # POSIX pin: the read path normalizes CRLF->LF (universal newlines) and
    # the write path keeps LF, so an edited CRLF file normalizes here; on
    # Windows the text-mode write translates back to CRLF (os.linesep).
    assert isi == "header\nwarna = 'terang'\nfooter\n"


def test_edit_file_file_lf_blok_crlf_berhasil_dan_tetap_lf(tmp_path):
    ex = _ex(tmp_path)
    ex.write_file("app.jsx", "header\nwarna = 'gelap'\nfooter\n")
    hasil = ex.edit_file("app.jsx", DIFF_CRLF)
    assert hasil.startswith("Success: edited file")
    isi = open(tmp_path / "app.jsx", "rb").read().decode("utf-8")
    assert isi == "header\nwarna = 'terang'\nfooter\n"


def test_edit_file_blok_sesuai_verbatim_tetap_jalur_cepat(tmp_path):
    ex = _ex(tmp_path)
    ex.write_file("a.txt", "x = 1\n")
    hasil = ex.edit_file(
        "a.txt",
        # size-changing replacement: coarse filesystem clocks can judge a
        # same-size rewrite as "unchanged" (see test_tool_evidence)
        "<<<<<<< SEARCH\nx = 1\n=======\nx = 22  # jalur cepat\n>>>>>>> REPLACE",
    )
    assert hasil.startswith("Success: edited file")
    assert "x = 22" in open(tmp_path / "a.txt").read()


def test_edit_file_gagal_menyebut_ketiga_format_line_ending(tmp_path):
    ex = _ex(tmp_path)
    ex.write_file("a.txt", "isi asli\r\n")
    hasil = ex.edit_file(
        "a.txt",
        "<<<<<<< SEARCH\nblok yang tidak ada\n=======\nx\n>>>>>>> REPLACE",
    )
    assert hasil.startswith("Error: SEARCH block not found")
    assert "verbatim, as LF and as CRLF" in hasil


def test_edit_file_gagal_memberi_panduan_baca_ulang_regio(tmp_path):
    ex = _ex(tmp_path)
    ex.write_file("a.txt", "isi asli\n")
    hasil = ex.edit_file(
        "a.txt",
        "<<<<<<< SEARCH\nblok yang tidak ada\n=======\nx\n>>>>>>> REPLACE",
    )
    assert "TRUNCATED" in hasil
    assert "read_file_range" in hasil
    assert "Do NOT rewrite the whole file" in hasil


# ------------------------------------------- empty stdout is not a smile


def test_run_terminal_stdout_kosong_menyebut_sangkaan_quoting(tmp_path):
    hasil = _ex(tmp_path).run_terminal("true")
    assert "stdout was EMPTY" in hasil
    assert "nested quotes" in hasil
    assert hasil != "Success, no output."


def test_run_terminal_beroutput_tetap_dikembalikan_apa_adanya(tmp_path):
    hasil = _ex(tmp_path).run_terminal("echo halo-orion")
    assert hasil == "halo-orion"


def test_run_terminal_exit_nol_kosong_tetap_dihitung_terminal_ok(tmp_path):
    """The agent loop counts non-error terminal results as work evidence;
    an empty-but-successful run still RAN, so the count must not change."""
    hasil = _ex(tmp_path).run_terminal("true")
    assert not hasil.startswith(("Error", "❌", "[REJECTED"))


def test_hasil_tool_kosong_berubah_jadi_diagnostik(tmp_path):
    """The second smile trap: ANY tool returning an empty string used to
    become 'Executed successfully, no output.' in the transcript."""
    import json as _json

    from app.services.test_computed_style_gate import _jalankan
    from app.services.test_reference_persistence import _TERef
    from app.services.test_soft_limit_reanchor import _Resp, _tool_call

    class _TEKosong(_TERef):
        def search_code(self, query: str, file_pattern: str = "*") -> str:
            return ""

    _frames, _state, deps = _jalankan(
        "cari kata foo di proyek",
        [
            _Resp("", tool_calls=[
                _tool_call("search_code", _json.dumps({"query": "foo"}))]),
            _Resp("Pencarian tidak menemukan apa pun; tidak ada yang diubah."),
        ],
        executor=_TEKosong(tmp_path),
    )
    isi = [
        str(m.get("content", ""))
        for m in deps.context_manager.history
        if m.get("role") == "tool"
    ]
    assert any("EMPTY output and no error" in t for t in isi)
    assert not any(t == "Executed successfully, no output." for t in isi)

"""
test_tool_evidence.py — per-file disk evidence + auto-syntax (UPDATE-022 Commit 2)
====================================================================================
Pins the second half of the InvoiceFlow M1 defect class: the tool-result
string itself must carry per-file proof. A "Success" that did not change the
file comes back as an explicit Error (so the Commit-1 ledger records a
FAILURE and the claim gate can challenge it), a healthy success carries the
(mtime_ns, size) before -> after stamp, and every successful .py write/edit
gets an immediate syntax verdict appended — visible to the model AND to
session_history the instant it happens.
"""

from __future__ import annotations

from app.services import code_verifier
from app.services.tool_executor import ToolExecutor

DIFF_VALID = (
    "<<<<<<< SEARCH\n"
    "x = 1\n"
    "=======\n"
    "x = 2\n"
    ">>>>>>> REPLACE"
)

# Size-changing variant: guarantees the (mtime_ns, size) tuple moves even on
# coarse-granularity filesystem clocks when the write lands in the same tick.
DIFF_MEMANJANG = (
    "<<<<<<< SEARCH\n"
    "x = 1\n"
    "=======\n"
    "x = 2  # diperpanjang agar ukuran berubah\n"
    ">>>>>>> REPLACE"
)

DIFF_RUSAK = (
    "<<<<<<< SEARCH\n"
    "x = 1\n"
    "=======\n"
    "x = (1\n"
    ">>>>>>> REPLACE"
)


def _ex(tmp_path) -> ToolExecutor:
    return ToolExecutor(str(tmp_path))


def _tanpa_tulis(ex: ToolExecutor) -> ToolExecutor:
    """Simulate a write that reports success but never touches the disk."""
    ex._tulis_ke_disk = lambda full_path, content: None  # noqa: SLF001 (test seam)
    return ex


# ---------------------------------------------------------------- evidence

def test_write_file_baru_memuat_bukti_mtime_dan_size(tmp_path):
    hasil = _ex(tmp_path).write_file("a.txt", "hello")
    assert hasil.lower().startswith("success")
    assert "(evidence: mtime_ns - -> " in hasil       # new file: no 'before'
    assert "size - -> 5 bytes)" in hasil


def test_write_file_timpa_bukti_berubah(tmp_path):
    ex = _ex(tmp_path)
    ex.write_file("a.txt", "hello")
    hasil = ex.write_file("a.txt", "hello world yang lebih panjang")
    assert hasil.lower().startswith("success")
    assert "(evidence: mtime_ns " in hasil and " -> " in hasil
    assert "- ->" not in hasil.split("(evidence:")[1]  # both sides are numbers
    assert "size 5 -> 30 bytes)" in hasil


def test_edit_file_sukses_memuat_bukti(tmp_path):
    ex = _ex(tmp_path)
    ex.write_file("m.py", "x = 1\n")
    hasil = ex.edit_file("m.py", DIFF_MEMANJANG)
    assert hasil.lower().startswith("success")
    assert "1 block(s) replaced." in hasil
    assert "(evidence: mtime_ns " in hasil


def test_write_file_tak_berubah_di_disk_jadi_error(tmp_path):
    """THE no-op guard: success reported but mtime+size stale and content
    differs -> explicit Error (ledger material for the claim gate)."""
    ex = _ex(tmp_path)
    ex.write_file("a.txt", "konten lama")
    hasil = _tanpa_tulis(ex).write_file("a.txt", "konten baru")
    assert hasil.startswith("Error: file unchanged on disk")
    assert "Treat this edit as FAILED" in hasil


def test_edit_file_tak_berubah_di_disk_jadi_error(tmp_path):
    ex = _ex(tmp_path)
    ex.write_file("m.py", "x = 1\n")
    hasil = _tanpa_tulis(ex).edit_file("m.py", DIFF_VALID)
    assert hasil.startswith("Error: file unchanged on disk")


def test_write_konten_identik_jadi_sukses_jujur(tmp_path):
    """Coarse filesystem clocks: stat unchanged BUT the file already holds
    exactly the intended content -> honest Success via the content check,
    never a false FAILED."""
    ex = _ex(tmp_path)
    ex.write_file("a.txt", "sama persis")
    hasil = _tanpa_tulis(ex).write_file("a.txt", "sama persis")
    assert hasil.lower().startswith("success")
    assert "verified on disk with EXACTLY the intended content" in hasil


# --------------------------------------------------------------- auto-syntax

def test_auto_syntax_menangkap_py_rusak_seketika(tmp_path):
    hasil = _ex(tmp_path).write_file("src/b.py", "def f(:\n    pass\n")
    assert hasil.lower().startswith("success")          # the write DID happen
    assert "AUTO SYNTAX CHECK" in hasil
    assert "PYTHON SYNTAX line 1" in hasil
    assert "def f(:" in hasil                            # quoted source line


def test_auto_syntax_senyap_saat_bersih(tmp_path):
    hasil = _ex(tmp_path).write_file("src/ok.py", "def f():\n    return 1\n")
    assert hasil.lower().startswith("success")
    assert "AUTO SYNTAX" not in hasil


def test_auto_syntax_hanya_untuk_py(tmp_path):
    hasil = _ex(tmp_path).write_file("notes.md", "def f(:\n")
    assert hasil.lower().startswith("success")
    assert "AUTO SYNTAX" not in hasil


def test_auto_syntax_jalan_di_edit_file(tmp_path):
    ex = _ex(tmp_path)
    ex.write_file("m.py", "x = 1\n")
    hasil = ex.edit_file("m.py", DIFF_RUSAK)
    assert hasil.lower().startswith("success")
    assert "AUTO SYNTAX CHECK" in hasil
    assert "'(' was never closed" in hasil


# ------------------------------------------------------------------ contracts

def test_kontrak_prefix_sukses_dan_gagal_tetap(tmp_path):
    """The agent loop's evidence branch keys on the 'success' prefix
    (UPDATE-004 contract) — the evidence suffix must not disturb it, and
    every failure shape must stay outside it."""
    ex = _ex(tmp_path)
    assert ex.write_file("a.txt", "x").lower().startswith("success")
    assert ex.edit_file("a.txt", DIFF_VALID).startswith("Error:")   # SEARCH not found
    assert ex.edit_file("tidak_ada.py", DIFF_VALID).startswith("Error:")
    assert ex.write_file("../lolos.txt", "x").startswith("Error:")  # path guard


# ------------------------------------------------------------ pure helpers

def test_cek_sintaks_py_murni():
    assert code_verifier.cek_sintaks_py("a.py", "x = 1\n") == ""
    temuan = code_verifier.cek_sintaks_py("a.py", "def f(:\n    pass\n")
    assert "PYTHON SYNTAX line 1" in temuan
    assert "def f(:" in temuan


def test_bukti_string_murni():
    assert ToolExecutor._bukti_string(None, (7, 3)) == (
        "(evidence: mtime_ns - -> 7, size - -> 3 bytes)")
    assert ToolExecutor._bukti_string((5, 2), (7, 3)) == (
        "(evidence: mtime_ns 5 -> 7, size 2 -> 3 bytes)")


def test_putusan_bukti_sehat_mengembalikan_none(tmp_path):
    ex = _ex(tmp_path)
    ex.write_file("a.txt", "x")
    assert ex._putusan_bukti("a.txt", str(tmp_path / "a.txt"), "x", (1, 1), (2, 1)) is None

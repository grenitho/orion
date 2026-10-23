"""
test_inbox_manager.py — guards for the UPDATE-018 inbox drop-zone
=================================================================
Pins the contract the /chat wiring and the owner rely on:

  1. classification + extraction per family (text/code, image, pdf, docx,
     xlsx) with SOFT parsers: a missing parser degrades to a per-file
     'skip' warning, never to an exception;
  2. hard caps (20 MB/file, 7 MB/image, 60k chars, 30 PDF pages, 10 DOCX
     tables, 200 sheet rows) and — equally important — every cap that cuts
     content away ANNOUNCES itself inside the result;
  3. processed/broken files MOVE to .orion/inbox/archive/ (never deleted);
     unsupported/oversized files STAY and are warned about exactly once
     per file version (name+size+mtime manifest in .orion/inbox_state.json);
  4. the NEVER-RAISE contract: unreadable folders, corrupt manifests,
     failing parsers and failing archive moves all come back as warning
     strings;
  5. describe() (frozen 📥 status frame) and context_block() (this-turn
     context injection, including warnings-only results — review fix a).

No network, no LLM: pure filesystem behaviour inside tmp_path.
"""

from __future__ import annotations

import base64
import json

import pytest

from app.services import inbox_manager as im

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
    "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.fixture()
def proyek(tmp_path):
    """A temp project with an empty .orion/inbox/."""
    inbox = tmp_path / ".orion" / "inbox"
    inbox.mkdir(parents=True)
    return tmp_path


def state_path(proyek):
    return proyek / ".orion" / "inbox_state.json"


# --------------------------------------------------------------------------
# config knobs
# --------------------------------------------------------------------------

def test_read_inbox_config_default_dan_clamp():
    assert im.read_inbox_config(None) == (True, 5)
    assert im.read_inbox_config({}) == (True, 5)
    assert im.read_inbox_config({"inbox_max_files": 99}) == (True, 20)
    assert im.read_inbox_config({"inbox_max_files": 0}) == (True, 1)
    assert im.read_inbox_config({"inbox_max_files": -3}) == (True, 1)
    assert im.read_inbox_config({"inbox_enabled": False}) == (False, 5)
    # garbage types fall back to the default, never raise
    assert im.read_inbox_config({"inbox_max_files": "banyak"}) == (True, 5)
    assert im.read_inbox_config({"inbox_max_files": True}) == (True, 5)
    assert im.read_inbox_config({"inbox_max_files": 3.7}) == (True, 5)


# --------------------------------------------------------------------------
# extract_file: classification + caps
# --------------------------------------------------------------------------

def test_extract_text_family(tmp_path):
    f = tmp_path / "catatan.txt"
    f.write_text("halo inbox\n", encoding="utf-8")
    kind, payload = im.extract_file(f)
    assert kind == "text" and payload == "halo inbox\n"


def test_extract_text_lebih_60k_char_dipotong_dengan_pengumuman(tmp_path):
    f = tmp_path / "besar.md"
    f.write_text("a" * (im.MAX_CHARS + 5000), encoding="utf-8")
    kind, payload = im.extract_file(f)
    assert kind == "text"
    assert "truncated" in payload and str(im.MAX_CHARS) in payload
    assert len(payload) < im.MAX_CHARS + 300  # cap + a short note, not more


def test_extract_gambar_kecil(tmp_path):
    f = tmp_path / "piksel.png"
    f.write_bytes(PNG_1PX)
    kind, payload = im.extract_file(f)
    assert kind == "image"
    mime, b64 = payload
    assert mime == "image/png"
    assert base64.b64decode(b64) == PNG_1PX


def test_gambar_di_atas_7mb_ditolak_bukan_meracuni_panggilan(tmp_path):
    """Review fix b: Gemini's inline vision limit is 7 MB (attachments.py)."""
    f = tmp_path / "raksasa.png"
    with open(f, "wb") as fh:
        fh.truncate(im.MAX_BYTES_GAMBAR + 1)
    kind, reason = im.extract_file(f)
    assert kind == "skip"
    assert "7 MB" in reason


def test_file_lebih_20mb_di_skip(tmp_path):
    f = tmp_path / "raksasa.txt"
    with open(f, "wb") as fh:
        fh.truncate(im.MAX_BYTES + 1)
    kind, reason = im.extract_file(f)
    assert kind == "skip" and "20 MB" in reason


def test_file_kosong_di_skip(tmp_path):
    f = tmp_path / "kosong.txt"
    f.write_bytes(b"")
    kind, reason = im.extract_file(f)
    assert kind == "skip" and "empty" in reason


def test_tipe_tak_didukung_di_skip_dengan_daftar_dukungan(tmp_path):
    f = tmp_path / "arsip.zip"
    f.write_bytes(b"PK\x03\x04")
    kind, reason = im.extract_file(f)
    assert kind == "skip" and "unsupported" in reason and "pdf" in reason


def test_pdf_tanpa_parser_menjadi_skip_bukan_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(im, "_PdfReader", None)
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.4 fake")
    kind, reason = im.extract_file(f)
    assert kind == "skip" and "pypdf" in reason


def test_docx_tanpa_parser_menjadi_skip(tmp_path, monkeypatch):
    monkeypatch.setattr(im, "_DocxDocument", None)
    f = tmp_path / "doc.docx"
    f.write_bytes(b"PK\x03\x04 fake")
    kind, reason = im.extract_file(f)
    assert kind == "skip" and "python-docx" in reason


def test_xlsx_tanpa_parser_menjadi_skip(tmp_path, monkeypatch):
    monkeypatch.setattr(im, "_load_workbook", None)
    f = tmp_path / "sheet.xlsx"
    f.write_bytes(b"PK\x03\x04 fake")
    kind, reason = im.extract_file(f)
    assert kind == "skip" and "openpyxl" in reason


class _FakePage:
    def __init__(self, teks):
        self._teks = teks

    def extract_text(self):
        return self._teks


class _FakeReader:
    """Stand-in for pypdf.PdfReader: N pages of numbered lines."""

    def __init__(self, path):
        self.pages = [_FakePage(f"page {i}") for i in range(35)]


def test_pdf_dipotong_30_halaman_dengan_pengumuman(monkeypatch, tmp_path):
    """Review fix d: the page cap must announce itself."""
    monkeypatch.setattr(im, "_PdfReader", _FakeReader)
    f = tmp_path / "laporan.pdf"
    f.write_bytes(b"%PDF fake")
    kind, text = im.extract_file(f)
    assert kind == "text"
    assert "page 29" in text and "page 30" not in text
    assert "first 30 of 35 pages" in text


def test_pdf_rusak_menjadi_error_string_bukan_raise(monkeypatch, tmp_path):
    class _Boom:
        def __init__(self, path):
            raise ValueError("not a PDF")

    monkeypatch.setattr(im, "_PdfReader", _Boom)
    f = tmp_path / "rusak.pdf"
    f.write_bytes(b"%PDF fake")
    kind, reason = im.extract_file(f)
    assert kind == "error" and "PDF parse failed" in reason and "ValueError" in reason


def test_docx_nyata_paragraf_dan_tabel(tmp_path):
    docx = pytest.importorskip("docx")
    f = tmp_path / "surat.docx"
    doc = docx.Document()
    doc.add_paragraph("Baris pertama surat.")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "a"
    table.cell(0, 1).text = "b"
    table.cell(1, 0).text = "c"
    table.cell(1, 1).text = "d"
    doc.save(str(f))

    kind, text = im.extract_file(f)
    assert kind == "text"
    assert "Baris pertama surat." in text
    assert "a | b" in text and "c | d" in text


def test_docx_lebih_10_tabel_dipotong_dengan_pengumuman(tmp_path):
    """Review fix d: the DOCX table cap must announce itself."""
    docx = pytest.importorskip("docx")
    f = tmp_path / "tabel_banyak.docx"
    doc = docx.Document()
    for _ in range(12):
        doc.add_table(rows=1, cols=1)
    doc.save(str(f))

    kind, text = im.extract_file(f)
    assert kind == "text"
    assert "first 10 of 12 tables" in text


def test_xlsx_nyata_baris_dan_cap_200(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    f = tmp_path / "data.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    for i in range(250):
        ws.append([i, f"baris-{i}"])
    wb.create_sheet("Kedua").append(["jangan-dibaca"])
    wb.save(str(f))

    kind, text = im.extract_file(f)
    assert kind == "text"
    assert "baris-0" in text and "baris-199" in text
    assert "baris-200" not in text
    assert "truncated at 200 rows of sheet 'Sheet1'" in text
    assert "jangan-dibaca" not in text  # only the FIRST sheet


def test_extract_error_bila_read_gagal(tmp_path, monkeypatch):
    from pathlib import Path

    def _boom(self, **kwargs):
        raise OSError("disk on fire")

    monkeypatch.setattr(Path, "read_text", _boom)
    f = tmp_path / "x.txt"
    f.write_text("isi", encoding="utf-8")
    kind, reason = im.extract_file(f)
    assert kind == "error" and "cannot read file" in reason


# --------------------------------------------------------------------------
# ingest: the full turn cycle
# --------------------------------------------------------------------------

def test_ingest_siklus_penuh(proyek):
    inbox = proyek / ".orion" / "inbox"
    (inbox / "catatan.txt").write_text("halo\n", encoding="utf-8")
    (inbox / "piksel.png").write_bytes(PNG_1PX)
    (inbox / "arsip.zip").write_bytes(b"PK\x03\x04")

    r = im.ingest(str(proyek), None)

    assert r["names"] == ["arsip.zip", "catatan.txt", "piksel.png"]  # sorted
    assert [n for n, _ in r["texts"]] == ["catatan.txt"]
    assert [(n, m) for n, m, _ in r["images"]] == [("piksel.png", "image/png")]
    assert len(r["warnings"]) == 1 and "arsip.zip" in r["warnings"][0]

    # processed + broken files were ARCHIVED (moved, never deleted) ...
    archive = inbox / "archive"
    assert sorted(p.name for p in archive.iterdir()) == ["catatan.txt", "piksel.png"]
    # ... while the unsupported one STAYS in the inbox
    assert [p.name for p in inbox.iterdir() if p.is_file()] == ["arsip.zip"]

    # manifest: name + size + mtime for every TAKEN file (skip included)
    state = json.loads(state_path(proyek).read_text(encoding="utf-8"))
    assert set(state) == {"arsip.zip", "catatan.txt", "piksel.png"}
    assert set(state["catatan.txt"]) == {"size", "mtime"}


def test_ingest_idempoten_per_versi_file(proyek):
    inbox = proyek / ".orion" / "inbox"
    f = inbox / "catatan.txt"
    f.write_text("versi satu\n", encoding="utf-8")

    r1 = im.ingest(str(proyek), None)
    assert r1["names"] == ["catatan.txt"]

    # second turn, file unchanged (it was archived anyway): nothing new
    r2 = im.ingest(str(proyek), None)
    assert r2["names"] == [] and im.context_block(r2) == ""

    # a NEW version of the same name is picked up again
    f.write_text("versi dua\n", encoding="utf-8")
    r3 = im.ingest(str(proyek), None)
    assert r3["names"] == ["catatan.txt"]
    assert r3["texts"][0][1] == "versi dua\n"


def test_skip_hanya_diperingatkan_sekali_per_versi(proyek):
    inbox = proyek / ".orion" / "inbox"
    (inbox / "arsip.zip").write_bytes(b"PK\x03\x04")

    r1 = im.ingest(str(proyek), None)
    assert len(r1["warnings"]) == 1
    r2 = im.ingest(str(proyek), None)
    assert r2["warnings"] == [] and r2["names"] == []


def test_ingest_max_files_dan_sisanya_ditunda(proyek):
    inbox = proyek / ".orion" / "inbox"
    for i in range(4):
        (inbox / f"f{i}.txt").write_text(f"isi {i}\n", encoding="utf-8")

    r = im.ingest(str(proyek), {"inbox_max_files": 2})
    assert r["names"] == ["f0.txt", "f1.txt"]
    assert any("kept for later turns" in w for w in r["warnings"])

    r2 = im.ingest(str(proyek), {"inbox_max_files": 2})
    assert r2["names"] == ["f2.txt", "f3.txt"]


def test_ingest_matikan_lewat_config(proyek):
    (proyek / ".orion" / "inbox" / "a.txt").write_text("x", encoding="utf-8")
    r = im.ingest(str(proyek), {"inbox_enabled": False})
    assert r["names"] == [] and not state_path(proyek).exists()


def test_ingest_tanpa_proyek_atau_tanpa_inbox_aman(tmp_path):
    assert im.ingest(None, None)["names"] == []
    assert im.ingest("", None)["names"] == []
    assert im.ingest(str(tmp_path), None)["names"] == []  # no .orion/inbox dir


def test_ingest_folder_tak_terbaca_menjadi_warning(proyek, monkeypatch):
    from pathlib import Path

    (proyek / ".orion" / "inbox" / "a.txt").write_text("x", encoding="utf-8")

    def _boom(self):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "iterdir", _boom)
    r = im.ingest(str(proyek), None)
    assert r["names"] == []
    assert any("cannot read the inbox folder" in w for w in r["warnings"])


def test_state_rusak_json_dianggap_baru_tanpa_raise(proyek):
    inbox = proyek / ".orion" / "inbox"
    (inbox / "a.txt").write_text("x", encoding="utf-8")
    state_path(proyek).write_text("{bukan json", encoding="utf-8")
    r = im.ingest(str(proyek), None)
    assert r["names"] == ["a.txt"]


def test_state_mtime_bukan_angka_tidak_merobek_kontrak(proyek):
    """Review fix c: float('abc') inside the freshness check must not raise."""
    inbox = proyek / ".orion" / "inbox"
    f = inbox / "a.txt"
    f.write_text("x", encoding="utf-8")
    state_path(proyek).write_text(
        json.dumps({"a.txt": {"size": f.stat().st_size, "mtime": "abc"}}),
        encoding="utf-8",
    )
    r = im.ingest(str(proyek), None)
    assert r["names"] == ["a.txt"]  # treated as a new version, processed once


def test_archive_bentrok_nama_mendapat_sufiks_waktu(proyek):
    inbox = proyek / ".orion" / "inbox"
    archive = inbox / "archive"
    archive.mkdir(parents=True)
    (archive / "a.txt").write_text("versi lama", encoding="utf-8")
    (inbox / "a.txt").write_text("versi baru\n", encoding="utf-8")

    r = im.ingest(str(proyek), None)
    assert r["names"] == ["a.txt"]
    names = sorted(p.name for p in archive.iterdir())
    assert len(names) == 2
    assert "a.txt" in names  # the pre-existing archived copy is untouched
    bentrok = [n for n in names if n != "a.txt"][0]
    assert bentrok.startswith("a-") and bentrok.endswith(".txt")


def test_archive_gagal_hanya_warning_tidak_raise(proyek, monkeypatch):
    import shutil

    inbox = proyek / ".orion" / "inbox"
    (inbox / "a.txt").write_text("x", encoding="utf-8")

    def _boom(src, dst):
        raise OSError("cross-device link")

    monkeypatch.setattr(shutil, "move", _boom)
    r = im.ingest(str(proyek), None)
    assert r["texts"] and r["texts"][0][0] == "a.txt"  # content still injected
    assert any("could not archive" in w for w in r["warnings"])
    assert (inbox / "a.txt").exists()  # never deleted


def test_file_tersembunyi_dan_subfolder_diabaikan(proyek):
    inbox = proyek / ".orion" / "inbox"
    (inbox / ".DS_Store").write_text("x", encoding="utf-8")
    (inbox / "sub").mkdir()
    r = im.ingest(str(proyek), None)
    assert r["names"] == []


# --------------------------------------------------------------------------
# describe() + context_block()
# --------------------------------------------------------------------------

def test_describe_kosong_bila_tidak_ada_file_baru():
    assert im.describe({"names": []}) == ""
    assert im.describe({}) == ""


def test_describe_mencantumkan_nama_dan_lebihan():
    r = {"names": ["a.txt"]}
    assert im.describe(r) == "Inbox: 1 new file(s) this turn: a.txt"
    banyak = {"names": [f"f{i}.txt" for i in range(8)]}
    desc = im.describe(banyak)
    assert desc.startswith("Inbox: 8 new file(s)") and "+2" in desc


def test_context_block_kosong_hanya_bila_benar_benar_kosong():
    assert im.context_block({}) == ""
    assert im.context_block({"texts": [], "images": [], "warnings": [], "names": []}) == ""


def test_context_block_berlabel_dan_turn_ini_saja():
    r = {"texts": [("a.txt", "isi A"), ("b.md", "isi B")], "images": [], "warnings": [], "names": ["a.txt", "b.md"]}
    blok = im.context_block(r)
    assert "### INBOX FILE: a.txt\nisi A" in blok
    assert "### INBOX FILE: b.md\nisi B" in blok
    assert "THIS TURN ONLY" in blok
    assert ".orion/inbox/archive/" in blok


def test_context_block_warnings_tanpa_teks_tetap_sampai():
    """Review fix a: the model must be told what it cannot see — even when
    nothing could be extracted at all."""
    r = {"texts": [], "images": [], "warnings": ["inbox file 'x.pdf' failed to parse: boom"], "names": ["x.pdf"]}
    blok = im.context_block(r)
    assert "INBOX WARNINGS" in blok and "failed to parse" in blok


def test_context_block_menyebut_gambar_untuk_korelasi_vision():
    r = {"texts": [], "images": [("p.png", "image/png", "AAAA")], "warnings": [], "names": ["p.png"]}
    blok = im.context_block(r)
    assert "### INBOX IMAGE: p.png (image/png)" in blok
    assert "AAAA" not in blok  # base64 never travels through the text block

"""
test_jangkar_commit24.py — UPDATE-025 commit 24 teeth
=====================================================
InvoiceFlow transcript + owner screenshot (2026-11-01), second reading.
Three structural defects, three pins:

1. ONE-TURN MEMORY: compression re-summarized the previous summary each
   round (telephone game) until standing user orders vanished; the model
   answered as if only the last exchange existed. Pins: the per-turn
   TASK ANCHOR block (open todos + prior user messages verbatim + the
   stale-summary-ban override note) and the summary carry-over (prior
   summary verbatim + quoted unfinished orders).
2. BLIND SEARCH-MISS RETRIES: two consecutive edit failures rebuilt from
   stale memory. Pin: the miss error now carries the file's closest
   CURRENT region.
3. RITUAL COMMIT: every edit failed, npm build rewrote dist/, and the
   checkpoint commit manufactured "saved changes". Pin: git_commit
   refuses when every added/modified staged path is a build artifact
   (deletions exempt so hygiene untrack stays committable).
"""

from __future__ import annotations

import asyncio
import subprocess

from app.services import agent_loop
from app.services.tool_executor import ToolExecutor


class _TM:
    def __init__(self, todos):
        self._todos = todos

    def get_todos(self):
        return self._todos


def test_anchor_todo_dan_pesanan_verbatim():
    tm = _TM(
        [
            {"id": "t1", "text": "Sunting sidebar App.jsx", "status": "pending"},
            {"id": "t2", "text": "sudah beres", "status": "completed"},
        ]
    )
    history = [
        {"role": "user", "content": "[SUMMARY OF PREVIOUS SESSION]: lama"},
        {"role": "user", "content": "Kerjakan task 1: sunting sidebar App.jsx"},
        {"role": "assistant", "content": "siap"},
        {"role": "user", "content": "pandu saya untuk aktifkan backend"},
    ]
    blok = agent_loop.blok_jangkar_tugas(history, tm)
    assert "[TASK ANCHOR — THIS TURN ONLY]" in blok
    assert "Sunting sidebar App.jsx (pending)" in blok
    assert "sudah beres" not in blok
    # prior order quoted verbatim; the CURRENT request is skipped
    assert '"Kerjakan task 1: sunting sidebar App.jsx"' in blok
    assert "pandu saya untuk aktifkan backend" not in blok
    # summary present -> stale-ban override note rides along
    assert "STALE" in blok


def test_anchor_kosong_bila_tiada_jangkar():
    assert agent_loop.blok_jangkar_tugas([{"role": "user", "content": "halo"}], None) == ""
    assert agent_loop.blok_jangkar_tugas([], None) == ""


def test_anchor_tanpa_summary_tanpa_catatan():
    history = [
        {"role": "user", "content": "revisi subtab dulu"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "lanjutkan"},
    ]
    blok = agent_loop.blok_jangkar_tugas(history, None)
    assert '"revisi subtab dulu"' in blok
    assert "STALE" not in blok


class _CMStub:
    def __init__(self, split):
        self.split = split
        self.messages = []
        self.backups = 0

    def safe_split_index(self, history, target_index):
        return self.split

    def create_backup(self):
        self.backups += 1

    def clear_history(self):
        self.messages = []

    def add_message(self, role, content, **kw):
        self.messages.append({"role": role, "content": content, **kw})


def test_summarize_membawa_summary_lama_verbatim(monkeypatch):
    dilihat = {}

    async def palsu(messages):
        dilihat["window"] = messages
        return "PARA BARU"

    monkeypatch.setattr(agent_loop, "summarize_old_history", palsu)
    cm = _CMStub(split=2)
    history = [
        {"role": "user", "content": "[SUMMARY OF PREVIOUS SESSION]: RINGKASAN LAMA XYZ"},
        {"role": "user", "content": "Kerjakan task 1 sunting sidebar"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "pesan terkini"},
    ]
    hasil = asyncio.run(agent_loop.execute_summarize(cm, history, threshold=3))
    assert hasil is True
    # the prior summary must NOT be re-summarized (telephone game closed)
    isi_window = dilihat["window"]
    assert not any(
        str(m.get("content", "")).startswith("[SUMMARY OF PREVIOUS SESSION]")
        for m in isi_window
    )
    assert cm.backups == 1
    assert len(cm.messages) == 1 + 2  # new summary + the 2 recent messages
    gabung = cm.messages[0]["content"]
    assert gabung.startswith("[SUMMARY OF PREVIOUS SESSION]: ")
    assert "RINGKASAN LAMA XYZ" in gabung          # carried verbatim
    assert "PARA BARU" in gabung                    # fresh window paragraph
    assert '"Kerjakan task 1 sunting sidebar"' in gabung  # order quoted
    assert cm.messages[1]["content"] == "ok"
    assert cm.messages[2]["content"] == "pesan terkini"


def _repo(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_AUTHOR_NAME", "T")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@t")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "T")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@t")
    proyek = tmp_path / "proyek"
    (proyek / "src").mkdir(parents=True)
    (proyek / "src" / "main.py").write_text("print('hai')\n", encoding="utf-8")
    (proyek / "dist").mkdir()
    (proyek / "dist" / "a.js").write_text("v1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=proyek, check=True)
    subprocess.run(["git", "add", "-A"], cwd=proyek, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "awal"], cwd=proyek, check=True)
    return proyek


def test_git_commit_artefak_saja_ditolak_campuran_lolos(tmp_path, monkeypatch):
    proyek = _repo(tmp_path, monkeypatch)
    monkeypatch.setattr(ToolExecutor, "_hygiene_gitignore", lambda self: "")
    monkeypatch.setattr(ToolExecutor, "_hygiene_untrack", lambda self: "")
    exe = ToolExecutor(str(proyek))

    # only generated output changed -> the ritual-commit guard refuses
    (proyek / "dist" / "a.js").write_text("v2 hasil npm build\n", encoding="utf-8")
    hasil = exe.git_commit("checkpoint")
    assert hasil.startswith("Error: ritual-commit guard")
    assert "dist/a.js" in hasil

    # a real source change alongside -> checkpoint proceeds
    (proyek / "src" / "main.py").write_text("print('baru')\n", encoding="utf-8")
    hasil2 = exe.git_commit("checkpoint")
    assert hasil2.startswith("Success: git checkpoint created.")


def test_edit_miss_memberi_regio_terdekat(tmp_path):
    proyek = tmp_path / "p"
    proyek.mkdir()
    (proyek / "app.js").write_text(
        "function hitung(a, b) {\n"
        "  const total = hitungTotal(a, b);\n"
        "  return total;\n"
        "}\n",
        encoding="utf-8",
    )
    exe = ToolExecutor(str(proyek))
    diff_salah = (
        "<<<<<<< SEARCH\n"
        "  const total = hitungTotall(a, b);\n"
        "=======\n"
        "  const total = hitungTotal(a, b);\n"
        ">>>>>>> REPLACE"
    )
    hasil = exe.edit_file("app.js", diff_salah)
    assert hasil.startswith("Error: SEARCH block not found")
    assert "CLOSEST CURRENT REGION" in hasil
    assert "  const total = hitungTotal(a, b);" in hasil

    diff_ngawur = (
        "<<<<<<< SEARCH\n"
        "xyzzy plugh nothing like this file\n"
        "=======\n"
        "x\n"
        ">>>>>>> REPLACE"
    )
    hasil2 = exe.edit_file("app.js", diff_ngawur)
    assert hasil2.startswith("Error: SEARCH block not found")
    assert "CLOSEST CURRENT REGION" not in hasil2

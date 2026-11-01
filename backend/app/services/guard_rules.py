"""
guard_rules.py — claim & stream guard rules, split out so they CAN BE TESTED (ORION-34)
=======================================================================================
Before this module existed, two of Orion's most important mechanisms were
hidden inside a 1,200-line stream function, making them impossible to
unit-test:

  1. detecting a "work is complete" claim in the model's reply text, and
  2. deciding whether that claim must be CHALLENGED because no file changed.

Both are now pure functions: text/numbers in, boolean out. No FastAPI
imports, no state. The tests live in test_guard_rules.py and use REAL
hallucinated sentences that Orion once produced.
"""

from __future__ import annotations

import re

# Words/phrases that mark the model CLAIMING the work is complete.
# Expanded in ORION-24 after the original 8-word list proved to miss
# Indonesian claims such as "sudah tervalidasi" and "terimplementasi dengan baik".
#
# DELIBERATELY BILINGUAL (Phase D): chat replies follow the user's language by
# design, so claim detection must keep matching Indonesian keywords even
# though this codebase is now English. Do not "clean up" the Indonesian
# entries — test_guard_rules.py asserts on real production transcripts.
KATA_KLAIM = (
    "selesai",
    "done",
    "completed",
    "finished",
    "sudah saya",
    "telah saya",
    "berhasil",
    "task selesai",
    "terimplementasi",
    "sudah terpasang",
    "telah terpasang",
    "sudah terintegrasi",
    "telah terintegrasi",
    "terhubung dengan baik",
    "berjalan dengan baik",
    "siap digunakan",
    "siap pakai",
    "tervalidasi",
    "sudah tervalidasi",
    "dengan sukses",
    "tanpa error",
    "bebas error",
    "tidak ada error",
)


def kata_klaim(teks: str) -> bool:
    """True when the reply text contains a claim that the work is complete."""
    if not teks:
        return False
    rendah = teks.lower()
    return any(k in rendah for k in KATA_KLAIM)


# >>> UPDATE-025 commit 19 (field report 2026-10-31, InvoiceFlow dark
# theme: Orion ignored the COMPUTED-STYLE VERIFICATION challenge, closed,
# and claimed "sudah selesai dan diverifikasi pada file …" — a file read
# wearing the costume of visual verification). Narrow, negation-aware
# claim words: "unverified" / "belum terverifikasi" (the HONEST OR
# branch) must never trip this, so bare "verified" is deliberately NOT a
# keyword — only positively-framed verification claims are.
KLAIM_VISUAL = (
    "diverifikasi",
    "memverifikasi",
    "saya verifikasi",
    "sudah diperiksa",
    "telah diperiksa",
    "i verified",
    "verified by",
    "verified with",
    "verified on",
    "verified in",
    "verified against",
    "verified that",
)


def kalimat_klaim_visual(teks: str) -> str:
    """The first sentence of `teks` carrying a visual-verification claim
    (empty string = no claim). The challenge quotes it verbatim — the
    commit-14 lesson: a challenge that does not quote the real thing
    gets rationalized away as noise."""
    if not teks:
        return ""
    rendah = teks.lower()
    if not any(k in rendah for k in KLAIM_VISUAL):
        return ""
    for kalimat in re.split(r"(?<=[.!?\n])", teks):
        potongan = kalimat.strip()
        if potongan and any(k in potongan.lower() for k in KLAIM_VISUAL):
            return potongan[:200]
    return teks.strip()[:200]


KLAIM_IMPLEMENTASI = (
    "diimplementasikan",
    "diimplementasi",
    "saya implementasikan",
    "diterapkan",
    "saya terapkan",
    "implemented",
    "i added",
    "saya tambah",
    "added the",
    "added a",
    "added an",
)

# A negation immediately before the keyword turns a claim into an honest
# OR-branch sentence ("belum diimplementasikan", "not added") - such a
# sentence must NEVER trip the challenge.
_NEGASI_SEBELUM = ("belum", "tidak", "bukan", "not", "never", "no", "jangan")

KATA_PERINTAH_TOOL = (
    "playwright",
    "browser_navigate",
    "browser_click",
    "browser_evaluate",
    "browser_screenshot",
    "gunakan mcp",
    "pakai mcp",
    "use mcp",
    "lewat mcp",
    "via mcp",
)


def _ternegasi(rendah: str, idx: int) -> bool:
    kata = rendah[:idx].split()
    return bool(kata) and kata[-1] in _NEGASI_SEBELUM


def _kalimat_pertama_berkata(teks: str, katalog) -> str:
    """First sentence of `teks` carrying a NON-negated keyword hit
    (empty string = none). The challenge quotes it verbatim - the
    commit-14 lesson: an unquoted challenge gets rationalized away."""
    if not teks:
        return ""
    rendah = teks.lower()
    if not any(k in rendah for k in katalog):
        return ""
    for kalimat in re.split(r"(?<=[.!?\n])", teks):
        potongan = kalimat.strip()
        if not potongan:
            continue
        rp = potongan.lower()
        for k in katalog:
            idx = rp.find(k)
            if idx >= 0 and not _ternegasi(rp, idx):
                return potongan[:200]
    return ""


def kalimat_klaim_implementasi(teks: str) -> str:
    """UPDATE-025 commit 21 (InvoiceFlow exam, 2026-10-31): two turns
    closed with "telah diimplementasikan …" over a ledger holding ZERO
    mutated files. The first claiming sentence is what the challenge
    quotes back; negated forms stay honest-branch safe."""
    return _kalimat_pertama_berkata(teks, KLAIM_IMPLEMENTASI)


def kalimat_perintah_tool(teks: str) -> str:
    """First sentence of the user's instruction naming a tool the model
    is ordered to use (empty = no explicit order this turn)."""
    return _kalimat_pertama_berkata(teks, KATA_PERINTAH_TOOL)


# >>> UPDATE-025 commit 23 (InvoiceFlow transcript 2026-11-01): after the
# keyword-based teeth shipped, the field produced a closing that dodged
# EVERY keyword catalog ("telah diperiksa ... diverifikasi dengan
# verify_code ... build sukses") over a zero-mutation ledger. Keywords on
# the CLOSING are evolvable; the user's own INSTRUCTION is not. A turn
# whose instruction ordered work ("kerjakan ... sunting ... tambah ...")
# and whose ledger holds zero mutated files must end either with the
# edits or with a plain decline - never with a verification ritual.
KATA_KERJA_MUTASI = (
    "kerjakan",
    "sunting",
    "tambahkan",
    "tambah ",
    "ubah ",
    "perbaiki",
    "implement",
    "hapus",
    "pasang",
    "edit_file",
    "write_file",
    " add ",
    " change ",
    " fix ",
)

KATA_PENOLAKAN_JUJUR = (
    "tidak ada yang diubah",
    "tidak ada file yang diubah",
    "tidak melakukan perubahan",
    "tidak ada perubahan",
    "tidak mengubah",
    "belum ada pengerjaan",
    "tidak dipakai",
    "tidak digunakan",
    "tidak dijalankan",
    "tidak dapat",
    "tidak bisa",
    "tanpa pengamatan",
    "cannot",
    "could not",
    "was not used",
    "not used",
    "nothing was changed",
    "no changes",
    "nothing changed",
    "i did not change",
)


def kalimat_perintah_kerja(teks: str) -> str:
    """First sentence of the turn instruction ordering MUTATING work
    (empty = nothing was ordered that a file mutation would satisfy)."""
    return _kalimat_pertama_berkata(teks, KATA_KERJA_MUTASI)


def perintah_verifikasi_gaya(teks: str) -> tuple:
    """UPDATE-025 commit 29: (named-tool order sentence, does the
    instruction order getComputedStyle evidence). The 0.10.32 pure-verify
    exam proved a successful navigate ALONE 'honoured' the order while the
    ordered computed-style assertions never ran — navigation is not
    evidence. When the user's own text names getComputedStyle, the honour
    condition tightens to the computed-style ledger; the anchor sentence
    falls back to the getComputedStyle sentence so the challenge always
    quotes the user, never a keyword."""
    rendah = (teks or "").lower()
    butuh = "getcomputedstyle" in rendah
    perintah = kalimat_perintah_tool(teks or "")
    if not perintah and butuh:
        for kal in re.split(r"[\n;]+|\. ", teks or ""):
            if "getcomputedstyle" in kal.lower():
                perintah = kal.strip()[:300]
                break
    return perintah, butuh


def stagnasi_perlu_ditantang(
    nir_mutasi_beruntun: int,
    edit_gagal_beruntun: int,
    percobaan_edit: int,
    sudah_ditantang: int,
    maks_tantangan: int,
) -> bool:
    """UPDATE-025 commit 25: the stagnation-challenge decision. PURE.

    Fires from SIX consecutive iterations without one successful mutation
    OR THREE consecutive failed writes (the InvoiceFlow transcript of
    2026-11-01 thrashed ~10 failed edits of ``App.jsx`` across overlapping
    line ranges before closing unverified). Commit 26: the nir-mutation
    branch also needs at least ONE edit attempt this turn — a pure
    read/verify turn (revisions already in place) must not be punished.
    One-shot per turn, same soft philosophy as UPDATE-023: a challenge,
    not a loop.
    """
    return (
        (
            (nir_mutasi_beruntun >= 6 and percobaan_edit > 0)
            or edit_gagal_beruntun >= 3
        )
        and sudah_ditantang < maks_tantangan
    )


def ada_penolakan_jujur(teks: str) -> bool:
    """The honest OR branch: the closing plainly says nothing changed.
    Such a closing must NEVER trip the zero-mutation challenge."""
    return bool(teks) and any(k in teks.lower() for k in KATA_PENOLAKAN_JUJUR)


def klaim_perlu_ditantang(
    is_task_complete: bool,
    ada_file_berubah: bool,
    sudah_ditantang: int,
    maks_tantangan: int,
) -> bool:
    """Verification-gate decision (ORION-17/24).

    A claim is challenged ONLY if: the model claims completion, NO file
    changed on this turn, and the challenge quota is not exhausted yet. The
    quota exists so this gate cannot turn into a new loop.
    """
    return (
        bool(is_task_complete)
        and not ada_file_berubah
        and sudah_ditantang < maks_tantangan
    )


def klaim_edit_gagal_perlu_ditantang(
    is_task_complete: bool,
    daftar_edit_gagal: list,
    sudah_ditantang: int,
    maks_tantangan: int,
) -> bool:
    """Failed-edit claim gate (UPDATE-022, InvoiceFlow M1 §2C).

    A claim is challenged when the model claims completion while the edit
    ledger still holds failures that never succeeded afterwards — EVEN IF
    other files really were changed this turn. That partial-evidence case
    is exactly what slipped through ORION-17b: the old gate counted the
    PRESENCE of changes, not the match between the claim and the per-file
    evidence, so one unrelated real edit laundered a no-op "fixed" claim.
    `daftar_edit_gagal` is state.edit_gagal_belum_pulih(); the shared
    challenge quota (maks_tantangan) keeps this gate from becoming a loop,
    exactly like klaim_perlu_ditantang.
    """
    return (
        bool(is_task_complete)
        and bool(daftar_edit_gagal)
        and sudah_ditantang < maks_tantangan
    )


def stream_perlu_done_susulan(sudah_mengirim_done: bool) -> bool:
    """ORION-28 wrapper decision: send 'done' only if the stream ended without one."""
    return not sudah_mengirim_done


# ---------------------------------------------------------------------------
# >>> UPDATE-025 commit 9 (owner: "Orion harus bisa proaktif … tidak boleh
# seperti kuda yang ditutup matanya"): the OPEN FAILURE THREAD. A user
# report of a still-failing behaviour ("masih gagal", "tidak terhubung",
# "still fails") opens a thread that STAYS OPEN across turns until an
# assistant answer carries root-cause/options wording. While it is open,
# gate 5.5 challenges plain closings (TANTANG_SARAN) and the
# persistence-fact block (project_facts) may ride the final frame — so a
# structural cause ("this project has no database at all") can no longer
# sit invisible in plain sight while Orion answers only what was asked.
POLA_UTAS_GAGAL = re.compile(
    r"(?:masih|tetap|terus|still|keep|keeps)\s+(?:gagal|fail\w*|error\w*)"
    r"|tidak terhubung|belum bisa terhubung|cannot connect|connection refused"
    r"|could not connect|keeps failing|still fails?"
    # >>> UPDATE-025 commit 12 (owner: "saya dan user awam tidak selalu
    # ingat kalimat teknis"): a PLAIN data-loss complaint — no jargon,
    # just "my data is gone again after restart" — is a failure report
    # too. It opens the same advisory thread, so gate 5.5 owes a root
    # cause + options and the persistence-fact scan rides the answer:
    # the structural cause ("no database at all") answers the complaint
    # even when the owner never says the word SQLite.
    r"|(?:data|daftar|pelanggan|faktur|invoice|customer|record)s?\b.{0,40}?"
    r"(?:hilang|kosong|mundur|reset|tidak tersimpan|tidak kesimpan|tidak ke-simpan"
    r"|disappear\w*|gone|lost|missing)"
    r"|(?:hilang|kosong|tidak tersimpan|disappear\w*|gone|lost|missing).{0,40}?"
    r"(?:setelah|tiap|setiap|habis|every|after).{0,30}?(?:restart|mulai ulang|refresh|reload)",
    re.I | re.S,
)
KATA_SARAN = (
    "penyebab",
    "akar masalah",
    "root cause",
    "kemungkinan besar",
    "hipotesis",
    "opsi",
    "pilihan",
    "alternatif",
    "rekomendasi",
    "recommend",
    "saran saya",
    "saranku",
)
KATA_DATA = (
    "data",
    "pelanggan",
    "customer",
    "simpan",
    "save",
    "tambah",
    "add",
    "input",
    "create",
    "entry",
    "record",
)


def utas_gagal_dari(teks: str) -> str:
    """The failure-report snippet that opens/refreshes the thread, else ''."""
    if not teks:
        return ""
    m = POLA_UTAS_GAGAL.search(teks)
    if not m:
        return ""
    return teks[max(0, m.start() - 40): m.end() + 40].strip() or teks[:80]


def utas_tentang_data(teks: str) -> bool:
    """True when the open failure thread is about stored/added data — the
    shape that makes the persistence-layer fact relevant."""
    rendah = (teks or "").lower()
    return any(k in rendah for k in KATA_DATA)


# >>> UPDATE-025 commit 13 (owner: "Orion cenderung memakai bahasa Inggris
# walau user berbahasa Indonesia"): deterministic user-language detection.
# The SYSTEM_PROMPT LANGUAGE RULE already exists and still lost — because
# every deterministic block the loop appends (checkpoint template,
# disclosures) spoke English, so the model mirrored the dominant system
# voice. Detection lets those blocks speak the USER's language; lexical,
# cheap, and testable, like every other guard here.
KATA_ID = (
    "yang", "dan", "untuk", "dengan", "tidak", "sudah", "belum", "saya",
    "kita", "anda", "kamu", "tolong", "apakah", "kenapa", "mengapa",
    "berapa", "ini", "itu", "ada", "bisa", "mau", "jadi", "karena",
    "saat", "akan", "atau", "juga", "masih", "sekali", "mohon", "pakai",
    "buat", "kalau", "jika", "terima", "kasih", "sekalian", "dong",
)
KATA_EN = (
    " the ", " and ", " for ", " with ", "please", " i ", " my ", " we ",
    " you ", " is ", " are ", " want ", " need ", " because ", " when ",
    " what ", " how ", "thanks", " should ", " would ", " could ",
    " check ", " make ", " use ",
)


def deteksi_bahasa(teks: str) -> str:
    """'id' when the user's message reads Indonesian, else 'en'."""
    rendah = f" {(teks or '').lower()} "
    id_ = sum(rendah.count(f" {k} ") for k in KATA_ID)
    en_ = sum(rendah.count(k) for k in KATA_EN)
    return "id" if id_ > en_ else "en"


def kata_saran(teks: str) -> bool:
    """True when the reply carries root-cause/options wording — the
    proactivity that CLOSES the failure thread. Deliberately lexical:
    the thread closes on demonstrated advisory content, not on vibes."""
    if not teks:
        return False
    rendah = teks.lower()
    return any(k in rendah for k in KATA_SARAN)


def saran_perlu_ditantang(
    utas_aktif: bool,
    ada_saran: bool,
    sudah_ditantang: int,
    maks_tantangan: int,
) -> bool:
    """Gate 5.5 (UPDATE-025 commit 9): an open failure thread whose closing
    answer carries no root-cause/options wording gets ONE answer-first
    challenge per turn — same one-shot philosophy as UPDATE-023's soft
    claim challenge, so the gate cannot become a new loop."""
    return utas_aktif and not ada_saran and sudah_ditantang < maks_tantangan

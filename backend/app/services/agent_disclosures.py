"""
agent_disclosures.py — post-turn disclosures, verification gates, and task anchor
==================================================================================
Extracted from agent_loop.py to keep the core SSE loop focused on orchestration
while preserving every disclosure block, mid-turn verification gate, IPv4
fallback helper, and task-anchor formatter verbatim.
"""

from __future__ import annotations

import os
import re

from app import logging_setup
from app.services import guard_rules, project_facts, subagent_manager


def _L(state, en: str, idn: str) -> str:
    """UPDATE-025 commit 13: user-facing literals follow the detected
    turn language (state.bahasa_turn). The model mirrors the system
    voice; an English-only template out-votes the prompt's LANGUAGE
    RULE, so the templates now speak the user's language."""
    return idn if getattr(state, "bahasa_turn", "") == "id" else en


def _ui_berubah_dan_referensi(state, project_path) -> list:
    """UPDATE-025 commit 17: the shared PREMISE of the reference topic —
    UI files changed this turn AND saved reference attachments exist —
    WITHOUT the unread condition (commit 11's _referensi_belum_dibaca
    adds it). The computed-style tooth needs the premise even when the
    references WERE read: reading the handoff is not the same as
    looking at the rendered result. Returns the reference names
    (empty list = premise false).
    """
    if not getattr(state, "files_changed_this_turn", None):
        return []
    _UI = (".jsx", ".tsx", ".vue", ".css", ".scss", ".html")

    def _ada_ui(entri) -> bool:
        # ledger entries read "- `src/App.vue` (write_file)" — take the
        # backticked path, fall back to the raw string.
        _m = re.findall(r"`([^`]+)`", str(entri))
        _p = _m[0] if _m else str(entri)
        return _p.lower().endswith(_UI)

    if not any(_ada_ui(f) for f in state.files_changed_this_turn):
        return []
    dirr = os.path.join(project_path or ".", ".orion", "attachments")
    try:
        refs = sorted(os.listdir(dirr))
    except OSError:
        return []
    return refs


def _referensi_belum_dibaca(state, project_path) -> list:
    """UPDATE-025 commit 12: the shared condition behind BOTH the
    checkpoint disclosure (commit 11) and the in-turn reconciliation
    gate (commit 12): UI files changed this turn, saved reference
    attachments exist, none was read this turn. Returns the reference
    names (empty list = condition false). Commit 17: the premise
    (UI changed + references exist) lives in _ui_berubah_dan_referensi.
    """
    refs = _ui_berubah_dan_referensi(state, project_path)
    if not refs:
        return []
    if getattr(state, "lampiran_dibaca_turn_ini", None):
        return []
    return refs


def _perlu_nudge_referensi(state, tool_executor) -> bool:
    """One in-turn reconciliation challenge per turn (commit 12)."""
    if getattr(state, "nudge_referensi", 0):
        return False
    return bool(
        _referensi_belum_dibaca(state, getattr(tool_executor, "project_path", "."))
    )


def _kalimat_referensi_ada(state) -> str:
    """UPDATE-025 commit 15 (owner paste 2026-10-30, backend 0.10.18
    live: the reconciliation gate FIRED and the UNVERIFIED escalation
    shipped — and Orion still dodged the substance. It took the
    mid-turn challenge, "checked" with list_files on the PROJECT ROOT,
    concluded "FRONTEND_HANDOFF.md dan invoiceflow.html tidak
    ditemukan", and self-certified "fully follows standard responsive
    design…"). The gate injection listed BARE file names, so a
    root-scoped listing legally "proved" absence while the files sat
    in .orion/attachments/ the whole time. ONE source for the
    anti-evasion sentence shared by the gate injection and the
    checkpoint block: the attachments EXIST at their full relative
    paths, and an absence claim without a read_file call on that path
    is a false claim. Disclosure, not rejection.
    """
    return _L(
        state,
        "These files EXIST at those paths; read them with read_file — "
        "claiming they do not exist without ever calling read_file on "
        "that path is a false claim.",
        "File-file ini ADA di jalur tersebut; baca dengan read_file — "
        "mengklaim tidak ada tanpa pernah memanggil read_file pada jalur "
        "itu adalah klaim palsu.",
    )


def _blok_referensi_belum_dibaca(state, project_path) -> str:
    """UPDATE-025 commit 11 (owner incident: Orion invented indigo
    #4f46e5 while the green/gold handoff it was given sat unread): when
    UI files change on a turn that never opened the saved reference
    attachments, say so at the checkpoint. Deterministic: the reference
    list is the .orion/attachments/ directory itself, the consultation
    is this turn's read_file ledger. Disclosure, not rejection.
    Commit 15: the block already named full paths — it now also carries
    the shared anti-evasion sentence (_kalimat_referensi_ada), so both
    ends of the turn say the absence claim is false in the same words.
    """
    refs = _referensi_belum_dibaca(state, project_path)
    if not refs:
        return ""
    daftar = "\n".join(f"- `.orion/attachments/{r}`" for r in refs)
    if getattr(state, "bahasa_turn", "") == "id":
        teks = (
            "\n\n**⚠️ Lampiran referensi tersimpan TIDAK dibuka turn ini:**\n"
            + daftar
            + "\n\nFile UI berubah sementara referensi bawaan user ini tidak "
            "dibaca sama sekali turn ini. Baca ulang dengan read_file dan "
            "rekonsiliasi token desain (warna, font, spasi, layout) terhadap "
            "hasil render sebelum menyebut pekerjaan selesai. "
            + _kalimat_referensi_ada(state)
        )
    else:
        teks = (
            "\n\n**⚠️ Saved reference attachments were NOT consulted this "
            "turn:**\n"
            + daftar
            + "\n\nUI files changed while these user-provided references "
            "sat unread this turn. Re-read them with read_file and reconcile "
            "the design tokens (colors, fonts, spacing, layout) against the "
            "rendered result before calling the work done. "
            + _kalimat_referensi_ada(state)
        )
    if getattr(state, "nudge_referensi", 0):
        # >>> commit 12 escalation: the model was challenged INSIDE the
        # turn and closed anyway — the user must see that, not just the
        # plain gap.
        teks += _L(
            state,
            "\n\nOrion was reminded mid-turn (REFERENCE RECONCILIATION) "
            "and still closed without reading them: every design token "
            "in this turn's work must be treated as UNVERIFIED against "
            "the user's reference.",
            "\n\nOrion sudah diingatkan di tengah turn (REFERENCE "
            "RECONCILIATION) namun tetap menutup tanpa membacanya: semua "
            "token desain hasil turn ini harus dianggap BELUM "
            "TERVERIFIKASI terhadap referensi user.",
        )
    return teks


def _tool_browser_mcp(deps) -> set:
    """UPDATE-025 commit 17 (owner-approved Playwright MCP queue item;
    incident lineage: the UI rebuilt in invented indigo #4f46e5 and
    "verified" by `npm run build`, and the 2026-10-30 "fully follows
    standard responsive design…" self-certification): namespaced
    browser tools connected this turn (mcp__<server>__browser_*).
    Empty set without MCP or without a Playwright-style server — the
    whole tooth is silent unless the owner actually plugged one in via
    <project>/.orion/mcp.json (zero backend code to connect, UPDATE-014
    contract). Detection is by TOOL NAME, not server name: the owner
    picks the server key freely."""
    mcp = getattr(deps, "mcp", None)
    if mcp is None:
        return set()
    try:
        return {str(n) for n in mcp.tool_names() if "__browser_" in str(n)}
    except Exception:
        return set()


def _token_desain_dari_referensi(project_path, maks: int = 14) -> list:
    """UPDATE-025 commit 17: deterministic design-token extraction —
    hex colors (#rrggbb / #rgb) found in the saved reference
    attachments, file order, deduplicated, capped. Generic on purpose:
    the backend NEVER hardcodes a project's palette; the user's own
    handoff under .orion/attachments/ is the source of truth, exactly
    like the reference list itself."""
    dirr = os.path.join(project_path or ".", ".orion", "attachments")
    try:
        nama = sorted(os.listdir(dirr))
    except OSError:
        return []
    token: list = []
    for n in nama:
        if not n.lower().endswith((".md", ".txt", ".html", ".css", ".json")):
            continue
        p = os.path.join(dirr, n)
        if not os.path.isfile(p):
            continue
        try:
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                isi = f.read()
        except OSError:
            continue
        for t in re.findall(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b", isi):
            t = t.lower()
            if t not in token:
                token.append(t)
            if len(token) >= maks:
                return token
    return token


def _browser_hasil_sukses(result) -> bool:
    """UPDATE-025 commit 26: a browser call honours a named order ONLY
    when its result is not an error-shaped payload. The 0.10.29 re-exam
    showed compliance theater: a navigate that died on the file: protocol
    came back as a plain result string, counted as 'order honoured', and
    closed the perintah_tool debt prematurely."""
    teks = str(result or "")
    if teks.lstrip().startswith("[MCP"):
        return False
    low = teks[:400].lower()
    return not any(
        m in low
        for m in (
            "error:",
            "error ",
            "failed",
            "failure",
            "blocked",
            "refused",
            "timeout",
            "err_",
            "cannot navigate",
            "protocol is blocked",
            "net::",
        )
    )


def _perlu_nudge_gaya(state, deps) -> bool:
    """UPDATE-025 commit 17: one COMPUTED-STYLE VERIFICATION challenge
    per turn — UI changed, saved references exist, browser MCP tools
    connected, no getComputedStyle evidence in this turn's ledger yet,
    and the challenge has not fired. Reading the reference satisfies
    the RECONCILIATION gate, never this one: source in ≠ pixels out."""
    if getattr(state, "nudge_gaya", 0):
        return False
    if getattr(state, "gaya_diperiksa_turn_ini", False):
        return False
    if not _tool_browser_mcp(deps):
        return False
    return bool(
        _ui_berubah_dan_referensi(
            state, getattr(deps.tool_executor, "project_path", ".")
        )
    )


def _perlu_tantang_klaim_implementasi(state, konten) -> bool:
    # >>> P6.3 follow-up (owner uji-1 2026-11-02): an INTERROGATIVE request
    # is not a work order — "di file mana itu diimplementasikan?" contains
    # a mutation verb but asks a QUESTION. Challenging it minted a phantom
    # [implementasi] debt on a pure information turn. The question check
    # runs FIRST and skips the whole challenge.
    if guard_rules.adalah_pertanyaan(getattr(state, "permintaan_turn_ini", "")):
        return False
    if not (
        state.tantang_klaim_implementasi < 1
        and not state.files_changed_this_turn
        and bool(guard_rules.kalimat_klaim_implementasi(konten))
    ):
        return False
    if guard_rules.kalimat_perintah_kerja(getattr(state, "permintaan_turn_ini", "")):
        return True
    rendah = (konten or "").lower()
    return any(k in rendah for k in _KLAIM_KERJA_ORANG_PERTAMA)


# >>> UPDATE-026 commit 39: first-person perfective work-claim markers —
# the hole the anchor gate must never open (a lie "saya implementasikan X"
# on an inspection turn still earns its challenge). <<<
_KLAIM_KERJA_ORANG_PERTAMA = (
    "saya implement",
    "saya terapkan",
    "saya tambah",
    "telah saya",
    "sudah saya",
    "i implemented",
    "i added",
    "i applied",
)


# >>> UPDATE-026 commit 40 (owner B12, 2026-11-03): the playwright browser
# reported net::ERR_CONNECTION_REFUSED for http://localhost:3000 while the
# owner's OWN browser reached the same dev server on the same machine.
# One deterministic, self-diagnosing tooth: a refused navigate is retried
# ONCE against the IPv4 loopback literal. If the dev server listens
# IPv4-only while the browser resolved localhost to ::1, the retry
# CONNECTS and the turn proceeds on real pixels; if the retry is refused
# too, the marker states that the browser's network view differs from the
# host (container/WSL2/proxy), so the transcript carries the diagnosis
# instead of a bare refusal. Capped twice per turn; a decline stays a
# decline (nothing here pays any debt or flips any ledger flag). <<<
_REFUSED_MCP = "ERR_CONNECTION_REFUSED"
_URL_ARG_KEYS = ("url", "href", "address")


def _url_fallback_ipv4(func_args) -> str | None:
    """The navigate URL with localhost swapped for 127.0.0.1, else None."""
    if not isinstance(func_args, dict):
        return None
    for k in _URL_ARG_KEYS:
        v = func_args.get(k)
        if isinstance(v, str) and "localhost" in v.lower():
            return re.sub(r"localhost", "127.0.0.1", v, flags=re.I)
    return None


async def _fallback_ipv4_bila_ditolak(state, deps, func_name, func_args, result):
    if not str(func_name).endswith("__browser_navigate"):
        return result
    if _REFUSED_MCP not in str(result):
        return result
    if getattr(state, "fallback_ipv4_count", 0) >= 2:
        return result
    baru = _url_fallback_ipv4(func_args)
    if not baru:
        return result
    state.fallback_ipv4_count = getattr(state, "fallback_ipv4_count", 0) + 1
    args2 = dict(func_args)
    for k in _URL_ARG_KEYS:
        if k in args2:
            args2[k] = baru
            break
    hasil2 = await deps.mcp.call(func_name, args2)
    sukses = _REFUSED_MCP not in str(hasil2)
    marker = _L(
        state,
        "\n[ORION IPv4 FALLBACK] navigate to localhost was refused "
        "(ERR_CONNECTION_REFUSED); auto-retried "
        + baru
        + " -> "
        + (
            "CONNECTED."
            if sukses
            else "refused too: the browser's network view differs from this "
            "host (container/WSL2/proxy suspected)."
        ),
        "\n[ORION IPv4 FALLBACK] navigate ke localhost ditolak "
        "(ERR_CONNECTION_REFUSED); ulang otomatis ke "
        + baru
        + " -> "
        + (
            "TERHUBUNG."
            if sukses
            else "ditolak juga: pandangan jaringan browser berbeda dari host "
            "ini (kontainer/WSL2/proxy terpandang)."
        ),
    )
    logging_setup.get_logger("orion.respon").info(
        "[RESPON] ipv4 fallback %s -> %s : %s",
        func_name,
        baru,
        "terhubung" if sukses else "ditolak",
    )
    return (hasil2 if sukses else result) + marker


_TOOL_MUTASI = ("write_file", "edit_file", "delete_file", "move_file")


def _perlu_tantang_perintah_tool(state) -> bool:
    """UPDATE-025 commit 21: the owner ordered "kali ini gunakan mcp
    playwright untuk mengamati visual frontendnya" and Orion closed
    without a single browser call - twice. A named tool order may be
    declined, but only plainly, never silently.

    UPDATE-025 commit 25 (transcript 2026-11-01): the single shot was
    PAID WITH WORDS — one challenge, one disclosure, turn closed, zero
    browser calls. While the named order still has ZERO browser calls the
    cap becomes TWO: the second shot names the carrying debt. Once any
    browser call lands the order is honoured and no shot is needed."""
    # >>> UPDATE-025 commit 29: honour = browser call AND, when the
    # instruction names getComputedStyle, the computed-style ledger. A
    # navigate-only turn keeps the challenge (and the debt) alive. <<<
    butuh_gaya = getattr(state, "perintah_butuh_gaya", False)
    honor = state.panggilan_browser_turn_ini > 0 and (
        not butuh_gaya or getattr(state, "gaya_diperiksa_turn_ini", False)
    )
    if honor:
        return False
    cap = 2 if state.panggilan_browser_turn_ini == 0 else 1
    return state.tantang_perintah_tool < cap and bool(state.perintah_tool_eksplisit)


def _perlu_tantang_nihil_mutasi(state, konten) -> bool:
    # >>> P6.3 follow-up: same interrogative exemption as above.
    permintaan = getattr(state, "permintaan_turn_ini", "")
    if guard_rules.adalah_pertanyaan(permintaan):
        return False
    # >>> P8.1: pure non-coding assistant tasks may be answered inline in chat
    # without mutating files on disk.
    if guard_rules.adalah_tugas_non_coding(permintaan):
        return False
    return (
        state.tantang_nihil_mutasi < 1
        and not state.files_changed_this_turn
        and bool(guard_rules.kalimat_perintah_kerja(state.permintaan_turn_ini))
        and not guard_rules.ada_penolakan_jujur(konten)
    )


def _blok_todo_nol_bukti(state) -> str:
    """UPDATE-025 commit 23: todo items completed THIS turn with zero work
    evidence at their completion moment (no file change, no successful
    terminal run, no sub-agent run). Previously this disclosure rode the
    CHECKPOINT ending ONLY - the 2026-11-01 transcript closed on
    JAWAB_NORMAL and the owner saw nothing. One emitter, BOTH endings,
    per the standing house rule."""
    _nol_bukti = [tid for tid, ev in state.bukti_todo.items() if not any(ev)]
    if not _nol_bukti:
        return ""
    _daftar_nol = "\n".join(f"- ⚠️ `[{tid}]`" for tid in _nol_bukti)
    return (
        "\n\n**⚠️ Items marked complete with NO work "
        "evidence this turn:**\n"
        f"{_daftar_nol}\n\n"
        "At the moment each item above was marked completed, "
        "this turn had recorded no file change, no successful "
        "terminal command and no sub-agent run. The work may "
        "exist from an earlier turn or outside Orion - verify "
        "these items manually before trusting this report."
    )


def _perlu_tantang_klaim_visual(state, deps, konten) -> bool:
    """UPDATE-025 commit 19 (field report 2026-10-31, InvoiceFlow dark
    theme): the computed-style challenge can be IGNORED — and Orion
    then closed claiming "sudah selesai dan diverifikasi pada file…",
    a file read wearing the costume of visual verification while the
    owner himself had to fix dark-mode text colors. One challenge per
    turn intercepts that exact sentence: same premise as the style
    disclosure (UI changed + saved references + browser tools + no
    getComputedStyle evidence) PLUS a positively-framed verification
    claim in the closing text (guard_rules.kalimat_klaim_visual —
    negation-aware, so the honest OR branch never trips it)."""
    if getattr(state, "tantang_klaim_visual", 0):
        return False
    if getattr(state, "gaya_diperiksa_turn_ini", False):
        return False
    if not _tool_browser_mcp(deps):
        return False
    if not _ui_berubah_dan_referensi(
        state, getattr(deps.tool_executor, "project_path", ".")
    ):
        return False
    return bool(guard_rules.kalimat_klaim_visual(konten))


def _blok_gaya_tanpa_verifikasi(state, deps) -> str:
    """UPDATE-025 commit 17: end-of-turn twin of the COMPUTED-STYLE
    VERIFICATION gate — ONE emitter on BOTH endings (house rule #4,
    born that way this time). Deterministic fate: fires when UI files
    changed, saved references exist, browser MCP tools are connected,
    and the turn's ledger holds no getComputedStyle evidence.
    Disclosure, not rejection — the model may still close; the user
    sees exactly which visual claims lack rendered-style evidence."""
    if getattr(state, "gaya_diperiksa_turn_ini", False):
        return ""
    if not _tool_browser_mcp(deps):
        return ""
    if not _ui_berubah_dan_referensi(
        state, getattr(deps.tool_executor, "project_path", ".")
    ):
        return ""
    teks = _L(
        state,
        "\n\n**⚠️ Visual claims were NOT verified against rendered "
        "styles:**\nUI files changed and browser tools are connected, "
        "but this turn's ledger holds no getComputedStyle evidence — "
        "the colors, fonts and spacing the user actually sees were "
        "never inspected. Treat every visual-compliance claim in this "
        "turn as UNVERIFIED against the rendered result.",
        "\n\n**⚠️ Klaim visual TIDAK diverifikasi terhadap gaya "
        "render:**\nFile UI berubah dan tool browser terhubung, tetapi "
        "ledger turn ini tidak memuat bukti getComputedStyle — warna, "
        "font dan spasi yang benar-benar dilihat user tidak pernah "
        "diperiksa. Anggap setiap klaim kepatuhan visual turn ini "
        "BELUM TERVERIFIKASI terhadap hasil render.",
    )
    if getattr(state, "nudge_gaya", 0):
        teks += _L(
            state,
            "\n\nOrion was reminded mid-turn (COMPUTED-STYLE "
            "VERIFICATION) and still closed without running it.",
            "\n\nOrion sudah diingatkan di tengah turn (VERIFIKASI GAYA "
            "TERKOMPUTASI) namun tetap menutup tanpa menjalankannya.",
        )
    # >>> UPDATE-025 commit 25: a gaya debt from an EARLIER turn closes
    # here — this disclosure IS the plain decline about an older debt
    # reaching the user (the OR branch paying out). A debt created THIS
    # turn stays open: it rides the next turn's TASK ANCHOR. <<<
    for _d in state.utang_verifikasi_terbuka():
        if _d.get("jenis") == "gaya" and _d.get("turn_dibuat", 0) < state.turn_ke:
            state.tutup_debt_verifikasi("gaya")
    return teks


def _blok_debt_verifikasi(state) -> str:
    """UPDATE-025 commit 25: end-of-turn twin for OPEN verification debts
    OTHER than the computed-style one (that has its own emitter above).
    Debts created THIS turn stay open (this block is the user's first
    sight of them); debts created in an EARLIER turn close here — the
    decline about an older debt reached the user, which is exactly the
    OR branch paying out. Returns '' when nothing but gaya is open."""
    terbuka = [d for d in state.utang_verifikasi_terbuka() if d.get("jenis") != "gaya"]
    if not terbuka:
        return ""
    baris = [
        f"- [{d.get('jenis')}] \"{str(d.get('perintah', '')).strip()}\""
        for d in terbuka[:6]
    ]
    for d in terbuka:
        if d.get("turn_dibuat", 0) < state.turn_ke:
            state.tutup_debt_verifikasi(str(d.get("jenis")))
    return _L(
        state,
        "\n\n**⚠️ Verification debt still open:** the ordered checks below "
        "were challenged this session but never landed in a ledger and "
        "were never plainly declined — treat every related claim as "
        "UNVERIFIED:\n" + "\n".join(baris),
        "\n\n**⚠️ Utang verifikasi masih terbuka:** pemeriksaan berperintah "
        "di bawah sudah ditantang sesi ini tetapi tidak pernah mendarat di "
        "ledger dan tidak pernah ditolak terang-terangan — anggap setiap "
        "klaim terkait BELUM TERVERIFIKASI:\n" + "\n".join(baris),
    )


def _blok_edit_gagal_akhir(state) -> str:
    """UPDATE-025 commit 14 (owner paste 2026-10-30: a one-line-comment
    request vanished — the edit failed with 'SEARCH block not found',
    TANTANG_EDIT_GAGAL fired, and Orion rationalized the rejection as
    'a system injection testing or noting an earlier context/retry
    simulation', then verified, committed and reported done WITHOUT
    ever retrying the edit). The mid-turn challenge now quotes the
    turn's real request and says it is not a simulation; this is the
    end-of-turn twin: failed edits that never recovered get named at
    BOTH turn endings, so 'build passed' can never stand in for
    'your change landed'. Single emitter, both endings, localized.
    """
    gagal = state.edit_gagal_belum_pulih()
    if not gagal:
        return ""
    daftar = "\n".join(f"- `{g['path']}` — {g['alasan']}" for g in gagal[:6])
    if len(gagal) > 6:
        daftar += f"\n- +{len(gagal) - 6} more"
    return _L(
        state,
        "\n\n**⚠️ Failed edits were NEVER recovered this turn:**\n"
        + daftar
        + "\n\nThe changes above were never successfully applied, so this "
        "turn's request may be UNFULFILLED even where the reply reads as "
        "done. Judge the claim against these files, not against a passing "
        "build.",
        "\n\n**⚠️ Edit gagal TIDAK PERNAH pulih sampai akhir turn:**\n"
        + daftar
        + "\n\nPerubahan di atas tidak pernah berhasil diterapkan, jadi "
        "permintaan turn ini mungkin BELUM TERPENUHI meski jawaban terbaca "
        "selesai. Nilai klaim terhadap file-file ini, bukan terhadap build "
        "yang lolos.",
    )


def _blok_persistensi_bila_perlu(state, tool_executor) -> str:
    """UPDATE-025 commit 9: the deterministic persistence-fact rider.

    Fires only when (a) an open failure thread exists, (b) it is about
    stored/added data, (c) the bounded project scan finds NO persistence
    marker at all, and (d) the block was not already delivered for this
    thread. Fact, not guess: the block states exactly what was checked.
    """
    if not getattr(state, "utas_gagal", ""):
        return ""
    if getattr(state, "fakta_persistensi_disampaikan", False):
        return ""
    if not guard_rules.utas_tentang_data(state.utas_gagal):
        return ""
    proj = getattr(tool_executor, "project_path", ".")
    if project_facts.ada_persistensi(proj):
        return ""
    state.fakta_persistensi_disampaikan = True
    return project_facts.blok_persistensi(
        proj, bahasa=getattr(state, "bahasa_turn", "") or "en"
    )


def _tutup_utas_bila_disarankan(state, konten) -> None:
    """The thread closes ONLY on demonstrated advisory wording in the
    model's own text — boilerplate blocks never close it for it."""
    if getattr(state, "utas_gagal", "") and guard_rules.kata_saran(konten):
        state.utas_gagal = ""


def _blok_delegasi_akhir(state, klaim: bool = False) -> str:
    """UPDATE-025 commit 10: ONE merged end-of-turn delegation block.

    Commit 8's pipeline-broke block and commit 5's claim disclosure
    could both fire on the same frame, stating the same gap twice and —
    worse — contradicting each other: a role dispatched-but-dead read
    "ended early (retries 2)" in one block and "zero successful
    delegate_* calls reached them" in the other (owner paste,
    2026-10-29: verifier died AFTER two retries yet the second block
    claimed nobody reached it). One block now, per-role fate accurate:

      * requested role with fate error/capped -> "ended early (status
        X, retries N)";
      * requested role with no fate at all    -> "was never
        dispatched" (mati branch) / bare line (claim-only branch);
      * header: pipeline-broke when anything died, else the
        requested-but-NOT-dispatched header, claim-gated;
      * exactly one guidance paragraph.
    """
    diminta = subagent_manager.roles_diminta(
        getattr(state, "permintaan_turn_ini", "") or ""
    )
    if not diminta:
        return ""
    nasib = getattr(state, "nasib_role", None) or {}
    mati = sorted(
        r for r in diminta if (nasib.get(r) or ("",))[0] in ("error", "capped")
    )
    belum = sorted(r for r in diminta if r not in nasib)
    if not mati and not (klaim and belum):
        return ""
    if mati:
        baris = "\n".join(
            _L(
                state,
                f"- ⚠️ `{r}` ended early (status {nasib[r][0]}, retries {nasib[r][1]})",
                f"- ⚠️ `{r}` mati lebih dulu (status {nasib[r][0]}, retries {nasib[r][1]})",
            )
            for r in mati
        )
        if belum:
            baris += "\n" + "\n".join(
                _L(
                    state,
                    f"- ⚠️ `{r}` was never dispatched",
                    f"- ⚠️ `{r}` tidak pernah dispatch",
                )
                for r in belum
            )
        paragraf = _L(
            state,
            "The conclusions above rest on the dead sub-run's salvaged "
            "partial findings plus main-agent self-checks — NOT the "
            "ordered independent pipeline.",
            "Kesimpulan di atas bertumpu pada temuan parsial selamatan "
            "dari sub-run yang mati plus pemeriksaan mandiri agen utama "
            "— BUKAN pipeline independen yang Anda perintahkan.",
        )
        if belum or klaim:
            paragraf += _L(
                state,
                " Self-run checks (builds, verify_*, pytest) are NOT "
                "independent verification.",
                " Pemeriksaan mandiri (build, verify_*, pytest) BUKAN "
                "verifikasi independen.",
            )
        paragraf += _L(
            state,
            " Dispatch the missing role(s) on a next turn, or tell the "
            "user plainly why they were skipped.",
            " Dispatch role yang hilang pada turn berikutnya, atau "
            "katakan terus terang kepada user kenapa role itu dilewati.",
        )
        return (
            _L(
                state,
                "\n\n**⚠️ Sub-agent pipeline broke this turn:**\n",
                "\n\n**⚠️ Pipeline sub-agent putus turn ini:**\n",
            )
            + f"{baris}\n\n{paragraf}"
        )
    baris = "\n".join(f"- ⚠️ `{r}`" for r in belum)
    return (
        _L(
            state,
            "\n\n**⚠️ Delegation requested but NOT dispatched this turn:**\n",
            "\n\n**⚠️ Delegasi diminta tetapi TIDAK dispatch turn ini:**\n",
        )
        + f"{baris}\n\n"
        + _L(
            state,
            "This turn's request named the role(s) above, but zero "
            "successful delegate_* calls reached them. Self-run checks "
            "(builds, verify_*, pytest) are NOT independent verification. "
            "Dispatch the missing role(s), or tell the user plainly why "
            "they were skipped.",
            "Permintaan turn ini menyebut role di atas, tetapi nol "
            "panggilan delegate_* yang sukses sampai ke mereka. "
            "Pemeriksaan mandiri (build, verify_*, pytest) BUKAN "
            "verifikasi independen. Dispatch role yang hilang, atau "
            "katakan terus terang kepada user kenapa mereka dilewati.",
        )
    )


# >>> UPDATE-025 hotfix (InvoiceFlow todo-hijack incident, 2026-10-28) <<<
# Two PURE helpers for the per-turn re-anchor state. The incident: a stale
# todo list from an earlier session ("fix line_total precision with
# Decimal") hijacked a turn whose actual user request was a frontend
# pricing question — the forced LANJUT_TODO nudge pushed the model into
# the old work, the soft limit then paused the turn with the literal
# template placeholder "[mention the last task]", and the owner's
# "lanjut" resumed blind: Orion reported Decimal work instead of
# answering the question. Both helpers are deterministic (no wall-clock
# values) so harness recordings stay byte-identical across runs.
def _permintaan_pengguna_terakhir(history) -> str:
    """The user's genuine request of the current turn.

    Walks the history newest-first and returns the first user message
    that is not a system injection ("[SYSTEM ...]" — warnings, nudges,
    continuations). Vision-parts content (a list of typed parts) is
    flattened to its text parts. '' when nothing qualifies; callers
    display a "(not captured)" fallback rather than guessing.
    """
    for msg in reversed(history or []):
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, list):
            content = " ".join(
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        content = str(content or "").strip()
        if not content or content.startswith("[SYSTEM"):
            continue
        return content
    return ""


def _catatan_tool(nama: str, args) -> str:
    """Deterministic one-line "tool key='value'" note for the soft-limit
    status. Shows only the FIRST argument, whitespace-normalized and
    truncated at 60 chars, so a write_file body can never blow up the
    warning frame. Never raises: a malformed args object degrades to the
    bare tool name."""
    try:
        if isinstance(args, dict) and args:
            kunci = next(iter(args))
            nilai = " ".join(str(args[kunci]).split())
            if len(nilai) > 60:
                nilai = nilai[:60] + "…"
            return f"{nama} {kunci}={nilai!r}"
        return str(nama)
    except Exception:
        return str(nama)


# >>> END OF UPDATE-025 hotfix helpers <<<


# >>> UPDATE-025 commit 24: TASK ANCHOR (InvoiceFlow transcript 2026-11-01)
# The "one-turn memory" symptom: every compression re-summarized the
# PREVIOUS summary into one dense paragraph, so a standing user order
# decayed into nothing after a few rounds — two turns later the model
# literally asked itself "what was the user's prompt in the previous
# turn?" and answered the owner as if only the last exchange existed.
# Two pure helpers below feed (a) a per-turn anchor block that rides the
# context message (never persisted, never summarized away) and (b) a
# verbatim order section carried inside every new summary. Quotes are
# labelled CONTEXT, not commands, so this cannot resurrect the stale-todo
# hijack the 2026-10-28 hotfix fixed.
def _teks_user(msg) -> str:
    """Normalized text of a user message ('' for injections / non-user)."""
    if not isinstance(msg, dict) or msg.get("role") != "user":
        return ""
    content = msg.get("content")
    if isinstance(content, list):
        content = " ".join(
            str(p.get("text", ""))
            for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        )
    content = str(content or "").strip()
    if not content or content.startswith("[SYSTEM") or content.startswith("[SUMMARY"):
        return ""
    return content


def _pesanan_verbatim(messages, batas: int = 3, lebar: int = 200) -> str:
    """Chronological verbatim quotes of genuine user messages in `messages`
    (oldest first, capped), for the summary carry-over section."""
    kutip = []
    for msg in messages or []:
        t = _teks_user(msg)
        if not t:
            continue
        kutip.append(f'- "{t[:lebar]}"')
        if len(kutip) >= batas:
            break
    return "\n".join(kutip)


def blok_jangkar_tugas(history, todo_manager=None, debt=None) -> str:
    """UPDATE-025 commit 24: the per-turn TASK ANCHOR block.

    Rides the per-turn context message (like the inbox and findings
    blocks): present on EVERY iteration, absent from persisted history,
    so compression can never eat it. Contains, when non-empty:
      1. open todos verbatim (id + text + status, max 6),
      2. the previous up-to-2 genuine user messages verbatim — labelled
         CONTEXT so the current request stays the authority (the
         stale-todo hijack protection stays intact),
      3. a stale-ban override note, ONLY when a [SUMMARY OF PREVIOUS
         SESSION] message exists: workflow bans inside compressed
         history (e.g. "bypass Playwright") lose to the user's latest
         explicit instruction,
      4. UPDATE-025 commit 25: OPEN VERIFICATION DEBTS — the user's own
         ordered sentences, unpaid from earlier turns (the 2026-11-01
         transcript closed with an ordered Playwright check never
         dispatched; a one-shot challenge paid with words must not be
         the end of the story).
    Returns '' when there is nothing to anchor (byte-identical stream).
    """
    bagian: list = []
    if todo_manager is not None:
        try:
            terbuka = [
                t
                for t in todo_manager.get_todos()
                if t.get("status") in ("pending", "in_progress")
            ]
        except Exception:
            terbuka = []
        if terbuka:
            baris = [
                f"- [{t.get('id')}] {str(t.get('text', '')).strip()[:200]}"
                f" ({t.get('status')})"
                for t in terbuka[:6]
            ]
            if len(terbuka) > 6:
                baris.append(f"+{len(terbuka) - 6} more")
            bagian.append(
                "OPEN TODOS (verbatim, still unfinished):\n" + "\n".join(baris)
            )
    # >>> UPDATE-025 commit 25: verification debts ride the anchor —
    # the user's OWN ordered sentences, unpaid. Anchored on the user's
    # words, not a vocabulary keyword (commit 23 lesson). <<<
    if debt:
        baris = [
            f"- [{d.get('jenis')}] {str(d.get('perintah', '')).strip()[:200]}"
            for d in debt[:6]
        ]
        bagian.append(
            "VERIFICATION DEBT (the user's own ordered sentences, still "
            "UNPAID from earlier turns): perform the ordered check with the "
            "named tool, OR decline it PLAINLY in your closing — carrying "
            "it silently is not an option:\n" + "\n".join(baris)
        )
    perintah: list = []
    lewati_current = False
    for msg in reversed(history or []):
        t = _teks_user(msg)
        if not t:
            continue
        if not lewati_current:
            lewati_current = True  # the current request: already visible above
            continue
        perintah.append(t[:300])
        if len(perintah) >= 2:
            break
    if perintah:
        bagian.append(
            "PRIOR USER MESSAGES, verbatim (memory anchor — CONTEXT only; the "
            "current request above is the authority, do NOT resume old work "
            "unless the current request says so):\n"
            + "\n".join(f'- "{p}"' for p in perintah)
        )
    ada_summary = any(
        isinstance(m, dict)
        and str(m.get("content", "")).startswith("[SUMMARY OF PREVIOUS SESSION]")
        for m in (history or [])
    )
    if ada_summary:
        bagian.append(
            "NOTE: [SUMMARY OF PREVIOUS SESSION] is compressed history. A "
            "workflow ban or tool restriction inside it (e.g. 'bypass X', 'do "
            "not use Y') is STALE unless the user repeated it later; the "
            "user's latest explicit instruction overrides it."
        )
    if not bagian:
        return ""
    return "\n\n[TASK ANCHOR — THIS TURN ONLY]\n" + "\n\n".join(bagian)


# >>> END OF UPDATE-025 commit 24 task anchor <<<

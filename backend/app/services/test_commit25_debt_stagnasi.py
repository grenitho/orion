"""UPDATE-025 commit 25 — verification-debt ledger, stagnation detector,
sub-agent reliability, planning-prose stripper.

Field evidence (InvoiceFlow transcript 2026-11-01): FOUR one-shot closing
challenges fired in ONE closing sequence and the model paid all of them
with a plain disclosure — zero browser calls in the whole turn; ~10 failed
edits thrashed overlapping line ranges across ~12 compressions. The teeth
below make an unpaid ordered verification EXPENSIVE instead of optional:
a session-scoped debt rides the TASK ANCHOR until a ledger entry pays it
or a decline about an OLDER debt reaches the user.
"""

from app.services import agent_loop, loop_guards, stream_utils
from app.services import subagent_manager
from app.services.session_state import SessionState


def _state(**kw):
    st = SessionState()
    for k, v in kw.items():
        setattr(st, k, v)
    return st


# --- debt ledger mechanics -------------------------------------------------

def test_debt_open_dedup_and_close():
    st = _state()
    st.catat_debt_verifikasi("gaya", "verifikasi via Playwright MCP")
    st.catat_debt_verifikasi("gaya", "duplikat harus diabaikan")
    assert len(st.utang_verifikasi_terbuka()) == 1
    assert st.utang_verifikasi_terbuka()[0]["perintah"] == (
        "verifikasi via Playwright MCP"
    )
    assert st.tutup_debt_verifikasi("gaya") is True
    assert st.utang_verifikasi_terbuka() == []
    assert st.debt_disampaikan == ["gaya"]
    assert st.tutup_debt_verifikasi("gaya") is False  # nothing left


def test_debt_catat_never_raises_on_garbage():
    st = _state()
    st.debt_verifikasi = "bukan list"  # corrupted ledger must not kill a turn
    st.catat_debt_verifikasi("gaya", "x")
    assert True


def test_cap_iterasi_maks_widens_per_open_debt():
    st = _state()
    assert st.cap_iterasi_maks(20) == 20
    st.catat_debt_verifikasi("gaya", "a")
    st.catat_debt_verifikasi("stall", "b")
    assert st.cap_iterasi_maks(20) == 28


def test_reset_per_turn_keeps_debt_and_advances_turn():
    st = _state()
    st.catat_debt_verifikasi("gaya", "a")
    st.nir_mutasi_beruntun = 9
    st.edit_gagal_beruntun = 4
    st.tantang_stagnasi = 1
    st.reset_per_turn()
    assert st.turn_ke == 1
    assert st.nir_mutasi_beruntun == 0
    assert st.edit_gagal_beruntun == 0
    assert st.tantang_stagnasi == 0
    assert len(st.utang_verifikasi_terbuka()) == 1  # SESSION-scoped


# --- anchor + emitters -----------------------------------------------------

def test_anchor_carries_debt_verbatim():
    debt = [{"jenis": "perintah_tool", "perintah": "gunakan mcp playwright",
             "turn_dibuat": 0}]
    blok = agent_loop.blok_jangkar_tugas([], None, debt)
    assert "VERIFICATION DEBT" in blok
    assert "gunakan mcp playwright" in blok


def test_anchor_byte_identical_without_debt():
    assert agent_loop.blok_jangkar_tugas([], None, None) == ""
    assert agent_loop.blok_jangkar_tugas([], None, []) == ""


def test_debt_emitter_same_turn_stays_open_older_turn_closes():
    st = _state(turn_ke=3, bahasa_turn="id")
    st.catat_debt_verifikasi("stall", "ubah kontras subtab")
    st.debt_verifikasi[0]["turn_dibuat"] = 3  # created THIS turn
    teks = agent_loop._blok_debt_verifikasi(st)
    assert "utang verifikasi masih terbuka" in teks.lower()
    assert len(st.utang_verifikasi_terbuka()) == 1  # still open
    st.debt_verifikasi[0]["turn_dibuat"] = 1  # aged one turn
    teks = agent_loop._blok_debt_verifikasi(st)
    assert "ubah kontras subtab" in teks
    assert st.utang_verifikasi_terbuka() == []  # decline delivered: closed


def test_debt_emitter_silent_when_only_gaya_open():
    st = _state(turn_ke=2)
    st.catat_debt_verifikasi("gaya", "x")
    assert agent_loop._blok_debt_verifikasi(st) == ""


# --- named-tool second shot -------------------------------------------------

def test_named_tool_order_gets_two_shots_while_zero_browser_calls():
    st = _state(perintah_tool_eksplisit="gunakan playwright")
    assert agent_loop._perlu_tantang_perintah_tool(st) is True
    st.tantang_perintah_tool = 1
    assert agent_loop._perlu_tantang_perintah_tool(st) is True  # 2nd shot
    st.tantang_perintah_tool = 2
    assert agent_loop._perlu_tantang_perintah_tool(st) is False


def test_named_tool_order_one_shot_once_browser_landed():
    st = _state(perintah_tool_eksplisit="gunakan playwright",
                panggilan_browser_turn_ini=1)
    assert agent_loop._perlu_tantang_perintah_tool(st) is False


# --- stagnation gate + breaker ----------------------------------------------

def _klaim_state(**kw):
    base = dict(
        permintaan_turn_ini="Task: ubah kontras subtab dan baris Total.",
        files_changed_this_turn=["frontend/src/App.jsx"],
        nir_mutasi_beruntun=6,
        edit_gagal_beruntun=3,
        edit_gagal=[{"urutan": 1, "path": "a.jsx", "alasan": "x"}],
        edit_sukses=[{"urutan": 2, "path": "a.jsx"}],  # recovered: Gate 2 silent
    )
    base.update(kw)
    return _state(**base)


def test_stagnation_gate_challenges_claim_once():
    st = _klaim_state()
    kep = loop_guards.evaluasi_akhir_iterasi(
        st, "Semua revisi selesai dan bekerja dengan baik.",
        iterasi=8, max_iter=20, sisa_todo=[], maks_nudge=2, maks_verifikasi=2,
    )
    assert kep == loop_guards.Keputusan.TANTANG_STAGNASI
    st.tantang_stagnasi = 1  # quota spent: the close may proceed
    kep = loop_guards.evaluasi_akhir_iterasi(
        st, "Semua revisi selesai dan bekerja dengan baik.",
        iterasi=8, max_iter=20, sisa_todo=[], maks_nudge=2, maks_verifikasi=2,
    )
    assert kep == loop_guards.Keputusan.CHECKPOINT


def test_stagnation_gate_silent_on_productive_turn():
    st = _klaim_state(nir_mutasi_beruntun=1, edit_gagal_beruntun=0)
    kep = loop_guards.evaluasi_akhir_iterasi(
        st, "Semua revisi selesai dan bekerja dengan baik.",
        iterasi=8, max_iter=20, sisa_todo=[], maks_nudge=2, maks_verifikasi=2,
    )
    assert kep == loop_guards.Keputusan.CHECKPOINT


def test_stagnation_gate_needs_mutation_verb_order():
    st = _klaim_state(permintaan_turn_ini="apa warna favoritmu?")
    kep = loop_guards.evaluasi_akhir_iterasi(
        st, "Selesai sudah jawabannya.",
        iterasi=8, max_iter=20, sisa_todo=[], maks_nudge=2, maks_verifikasi=2,
    )
    assert kep != loop_guards.Keputusan.TANTANG_STAGNASI


def test_breaker_only_on_thrash_combination():
    assert loop_guards.stagnasi_penuh(
        _state(nir_mutasi_beruntun=12, edit_gagal_beruntun=3)
    ) is True
    assert loop_guards.stagnasi_penuh(
        _state(nir_mutasi_beruntun=12, edit_gagal_beruntun=2)
    ) is False
    assert loop_guards.stagnasi_penuh(
        _state(nir_mutasi_beruntun=11, edit_gagal_beruntun=9)
    ) is False


def test_edit_success_breaks_failure_streak():
    st = _state()
    st.catat_edit_gagal("a.jsx", "SEARCH block not found")
    st.catat_edit_gagal("a.jsx", "SEARCH block not found")
    assert st.edit_gagal_beruntun == 2
    st.catat_edit_sukses("a.jsx")
    assert st.edit_gagal_beruntun == 0


# --- sub-agent reliability ---------------------------------------------------

def test_subagent_default_max_iter_raised():
    assert subagent_manager.DEFAULT_MAX_ITER == 10


def test_minicontext_drops_consecutive_duplicates():
    ctx = subagent_manager.MiniContext()
    ctx.add("user", "periksa kontras subtab")
    ctx.add("user", "periksa kontras subtab")  # wire-level duplicate
    ctx.add("assistant", "saya cek dulu")
    ctx.add("tool", "hasil baca file")
    ctx.add("tool", "hasil baca file")
    msgs = ctx.messages()
    assert len(msgs) == 3
    assert [m["role"] for m in msgs] == ["user", "assistant", "tool"]


# --- planning-prose stripper --------------------------------------------------

def test_prose_perencanaan_catches_the_documented_leak():
    bocor = ("Let's check git_diff first to see what changed. "
             "Wait, what was the user's prompt again?")
    assert stream_utils.prose_perencanaan(bocor) is True
    assert stream_utils.prose_perencanaan(
        "Saya akan baca App.jsx lalu jalankan verifikasi subtab."
    ) is True


def test_prose_perencanaan_leaves_real_answers_alone():
    assert stream_utils.prose_perencanaan(
        "Revisi selesai. Subtab aktif kini memakai teks gelap di latar "
        "terang, dan baris Total live sudah rata kanan."
    ) is False
    assert stream_utils.prose_perencanaan("") is False
    assert stream_utils.prose_perencanaan(
        "Let's check git_diff\n```diff\n+ x\n```"
    ) is False  # carries user-facing markup: not pure monologue
    assert stream_utils.prose_perencanaan("x" * 700) is False

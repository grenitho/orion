"""test_commit40_ipv4_fallback_bahasa.py — UPDATE-026 commit 40 (0.10.44).

B12 (owner 2026-11-03): the playwright browser reported
net::ERR_CONNECTION_REFUSED for http://localhost:3000 while the owner's
own browser reached the same dev server on the same machine. Tooth: a
refused browser_navigate is retried ONCE against the 127.0.0.1 literal;
the marker either brings real pixels or names the container/WSL2/proxy
suspect in the transcript.

B13: "Kerjakan penambahan komentar header di frontend/src/App.jsx
sekarang." scored 0-0 on the lexical detector and fell through to 'en',
so disclosure blocks spoke English to an Indonesian owner. Teeth:
prepositions/imperatives in KATA_ID + an affix tie-break.
"""

import asyncio
import types

from app.services import agent_loop, guard_rules
from app.services.session_state import SessionState


class _McpPura:
    """Refuses localhost, serves 127.0.0.1 (the IPv4-only dev server)."""

    def __init__(self, refuse_ipv4_too: bool = False):
        self.refuse_ipv4_too = refuse_ipv4_too
        self.panggilan = []

    async def call(self, name, args):
        self.panggilan.append((name, dict(args)))
        url = args.get("url", "")
        if "127.0.0.1" in url and not self.refuse_ipv4_too:
            return "### Result\n<title>InvoiceFlow</title>\n### Ran Playwright code"
        return f"Error: net::ERR_CONNECTION_REFUSED at {url}"


def _deps(mcp):
    return types.SimpleNamespace(mcp=mcp)


# --------------------------------------------------------- B12: fallback


def test_url_fallback_ipv4():
    assert agent_loop._url_fallback_ipv4(
        {"url": "http://localhost:3000"}
    ) == "http://127.0.0.1:3000"
    assert agent_loop._url_fallback_ipv4(
        {"url": "http://LOCALHOST:3000/x"}
    ) == "http://127.0.0.1:3000/x"
    assert agent_loop._url_fallback_ipv4({"url": "http://127.0.0.1:3000"}) is None
    assert agent_loop._url_fallback_ipv4("bukan dict") is None


def test_fallback_menyambung_ke_ipv4():
    mcp = _McpPura()
    state = SessionState()
    state.bahasa_turn = "id"
    hasil = asyncio.run(
        agent_loop._fallback_ipv4_bila_ditolak(
            state,
            _deps(mcp),
            "mcp__playwright__browser_navigate",
            {"url": "http://localhost:3000"},
            "Error: net::ERR_CONNECTION_REFUSED at http://localhost:3000",
        )
    )
    assert "InvoiceFlow" in hasil
    assert "TERHUBUNG" in hasil
    assert state.fallback_ipv4_count == 1
    assert mcp.panggilan[-1][1]["url"] == "http://127.0.0.1:3000"


def test_fallback_ditolak_juga_menyebut_penyebab():
    mcp = _McpPura(refuse_ipv4_too=True)
    state = SessionState()
    state.bahasa_turn = "id"
    asli = "Error: net::ERR_CONNECTION_REFUSED at http://localhost:3000"
    hasil = asyncio.run(
        agent_loop._fallback_ipv4_bila_ditolak(
            state,
            _deps(mcp),
            "mcp__playwright__browser_navigate",
            {"url": "http://localhost:3000"},
            asli,
        )
    )
    assert hasil.startswith(asli)
    assert "kontainer/WSL2/proxy" in hasil
    assert state.fallback_ipv4_count == 1


def test_cap_dua_kali_per_turn():
    mcp = _McpPura()
    state = SessionState()
    state.fallback_ipv4_count = 2
    asli = "Error: net::ERR_CONNECTION_REFUSED at http://localhost:3000"
    hasil = asyncio.run(
        agent_loop._fallback_ipv4_bila_ditolak(
            state,
            _deps(mcp),
            "mcp__playwright__browser_navigate",
            {"url": "http://localhost:3000"},
            asli,
        )
    )
    assert hasil == asli
    assert mcp.panggilan == []


def test_non_navigate_tidak_disentuh():
    mcp = _McpPura()
    state = SessionState()
    asli = "Error: net::ERR_CONNECTION_REFUSED at http://localhost:3000"
    hasil = asyncio.run(
        agent_loop._fallback_ipv4_bila_ditolak(
            state,
            _deps(mcp),
            "mcp__playwright__browser_evaluate",
            {"url": "http://localhost:3000"},
            asli,
        )
    )
    assert hasil == asli
    assert mcp.panggilan == []


# --------------------------------------------------------- B13: bahasa


def test_perintah_teknis_pendek_terbaca_indonesia():
    assert guard_rules.deteksi_bahasa(
        "Kerjakan penambahan komentar header di frontend/src/App.jsx "
        "sekarang."
    ) == "id"
    assert guard_rules.deteksi_bahasa(
        "Buka frontend/src/App.jsx dan jelaskan implementasi tabs/subtab "
        "di sekitar baris 35-38, lalu nyatakan fakta verifikasinya."
    ) == "id"


def test_bahasa_inggris_tetap_inggris():
    assert guard_rules.deteksi_bahasa(
        "Add a header comment to frontend/src/App.jsx now."
    ) == "en"
    assert guard_rules.deteksi_bahasa(
        "Please check the colors and fix them because the contrast is low."
    ) == "en"


def test_afiks_memenangkan_seri_nol_nol():
    # zero catalog hits either way; morphology carries the tie
    assert guard_rules.deteksi_bahasa("Menghapuskan noda membandel.") == "id"

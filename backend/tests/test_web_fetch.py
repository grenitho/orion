"""
test_web_fetch.py — regression test for P8.2 web_fetch metadata & JSON-LD extraction
"""

from __future__ import annotations

import socket
from unittest.mock import MagicMock, patch

from app.services.tool_executor import ToolExecutor


def _mock_response(html_text: str, content_type: str = "text/html; charset=utf-8"):
    resp = MagicMock()
    resp.status = 200
    resp.headers = {"Content-Type": content_type}
    resp.read.return_value = html_text.encode("utf-8")
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def test_web_fetch_ekstrak_metadata_dan_json_ld():
    """Halaman lowongan/produk modern tetap terbaca lewat <title>, meta, dan JSON-LD."""
    sample_html = """
    <html>
      <head>
        <title>Senior Python Backend Engineer - PT Contoh</title>
        <meta property="og:description" content="Lowongan Remote Python FastAPI gaji kompetitif." />
        <script type="application/ld+json">
          {"@type": "JobPosting", "title": "Senior Python Backend Engineer", "employmentType": "FULL_TIME"}
        </script>
      </head>
      <body>
        <nav>Menu Home Login Daftar</nav>
        <main>
          <h1>Deskripsi Pekerjaan</h1>
          <p>Membangun layanan API berbasis FastAPI dan PostgreSQL.</p>
        </main>
      </body>
    </html>
    """
    executor = ToolExecutor(".")
    fake_addr = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    with (
        patch("socket.getaddrinfo", return_value=fake_addr),
        patch("urllib.request.urlopen", return_value=_mock_response(sample_html)),
    ):
        hasil = executor.web_fetch("https://example.com/jobs/python-engineer")

    assert "Page Title: Senior Python Backend Engineer - PT Contoh" in hasil
    assert "Meta: Lowongan Remote Python FastAPI gaji kompetitif." in hasil
    assert 'Structured Data (JSON-LD): {"@type": "JobPosting"' in hasil
    assert "Membangun layanan API berbasis FastAPI dan PostgreSQL." in hasil
    assert "Menu Home Login Daftar" not in hasil


def test_web_fetch_blokir_ssrf_localhost():
    """Proteksi SSRF tetap menolak alamat lokal."""
    executor = ToolExecutor(".")
    hasil = executor.web_fetch("http://localhost:8000/health")
    assert "SSRF protection" in hasil

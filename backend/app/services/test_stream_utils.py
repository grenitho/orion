"""
test_stream_utils.py — test wrapper 'done' (ORION-28, dibuat ter-test di ORION-36)
==================================================================================
Menjaga tiga perilaku yang membedakan "berakhir normal" dari "crash":
  1. aliran tanpa done  -> tepat satu done ditambahkan di akhir
  2. aliran dengan done -> tidak ada done kedua
  3. aliran yang melempar exception -> TIDAK menambahkan done, sehingga
     extension tetap bisa melaporkan crash sebagai crash (ORION-12)
"""

from __future__ import annotations

import asyncio

import pytest

from app.services.stream_utils import PENANDA_DONE, buat_event_done, jamin_done


async def _aliran(*potongan):
    for p in potongan:
        yield p


async def _kumpul(aliran):
    return [p async for p in aliran]


def _jalan(korotin):
    """Jalankan korotin tanpa dependensi pytest-asyncio."""
    return asyncio.run(korotin)


def test_done_ditambahkan_bila_tidak_ada():
    keluar = _jalan(_kumpul(jamin_done(_aliran("data: a\n\n", "data: b\n\n"))))
    assert len(keluar) == 3
    assert PENANDA_DONE in keluar[-1]
    assert keluar[-1] == buat_event_done()


def test_done_tidak_dobel():
    masuk = ("data: a\n\n", buat_event_done())
    keluar = _jalan(_kumpul(jamin_done(_aliran(*masuk))))
    assert keluar == list(masuk)
    assert sum(PENANDA_DONE in k for k in keluar) == 1


def test_crash_tidak_disamarkan_sebagai_selesai():
    """Paling penting: stream yang mati karena exception TIDAK boleh terlihat
    seperti berakhir normal; kalau tidak, extension tidak bisa melaporkan crash."""
    async def aliran_mati():
        yield "data: setengah jalan\n\n"
        raise RuntimeError("backend meledak")

    with pytest.raises(RuntimeError):
        _jalan(_kumpul(jamin_done(aliran_mati())))


def test_done_hanya_di_akhir():
    keluar = _jalan(_kumpul(jamin_done(_aliran("data: x\n\n"))))
    assert PENANDA_DONE not in keluar[0]
    assert PENANDA_DONE in keluar[-1]

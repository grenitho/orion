"""
test_stream_utils.py — tests for the 'done' wrapper (ORION-28, made testable in ORION-36)
==========================================================================================
Guards the three behaviours that separate "ended normally" from "crashed":
  1. a stream without done -> exactly one done is appended at the end
  2. a stream with done    -> no second done
  3. a stream that throws  -> NO done is added, so the extension can still
     report a crash as a crash (ORION-12)
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
    """Run a coroutine without a pytest-asyncio dependency."""
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
    """Most important: a stream that dies from an exception MUST NOT look like
    it ended normally; otherwise the extension cannot report the crash."""
    async def aliran_mati():
        yield "data: setengah jalan\n\n"
        raise RuntimeError("backend meledak")

    with pytest.raises(RuntimeError):
        _jalan(_kumpul(jamin_done(aliran_mati())))


def test_done_hanya_di_akhir():
    keluar = _jalan(_kumpul(jamin_done(_aliran("data: x\n\n"))))
    assert PENANDA_DONE not in keluar[0]
    assert PENANDA_DONE in keluar[-1]

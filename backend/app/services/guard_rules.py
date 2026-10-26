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

r"""test_turn_assembler.py - regression test for TurnAssembler (Account 3, 2026-10-02).

Pure bookkeeping tests: no audio, no VAD, no I/O, no threads. TurnAssembler, TurnResult and the
CHUNK_SAMPLES / SAMPLE_RATE constants are extracted from tools\stream_asr_test.py with ast and
executed in a private namespace, so the real source is tested without importing the module
(importing it would pull in torch, sounddevice and the live pipeline). The file under test is
never modified.

The Driver below follows the contract in the TurnAssembler docstring: for every chunk, append the
chunk to .chunks if collecting, call tick(), then handle the VAD event.

Run: python tools\test_turn_assembler.py   (exit code 0 = all pass)
"""
from __future__ import annotations

import __future__ as _fut
import ast
import collections
import dataclasses
import math
import sys
import types
import typing
import unittest
from pathlib import Path

import numpy as np

SOURCE = Path(r"C:\Jarvis_Ted\tools\stream_asr_test.py")
_SAFE = (ast.Constant, ast.Name, ast.BinOp, ast.UnaryOp, ast.operator, ast.unaryop, ast.expr_context)
_WANTED_CLASSES = ("TurnAssembler", "TurnResult")


def load_units(path: Path) -> dict:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mod = types.ModuleType("asr_extract")
    sys.modules["asr_extract"] = mod
    ns = mod.__dict__
    ns.update(np=np, dataclass=dataclasses.dataclass, field=dataclasses.field, dataclasses=dataclasses,
              typing=typing, NamedTuple=typing.NamedTuple, Optional=typing.Optional, List=typing.List,
              Tuple=typing.Tuple, Any=typing.Any, namedtuple=collections.namedtuple)
    flags = _fut.annotations.compiler_flag
    for node in tree.body:
        is_class = isinstance(node, ast.ClassDef) and node.name in _WANTED_CLASSES
        is_const = (isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None
                    and all(isinstance(x, _SAFE) for x in ast.walk(node.value)))
        if not (is_class or is_const):
            continue
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)
        try:
            exec(compile(module, str(path), "exec", flags=flags, dont_inherit=True), ns)
        except Exception as exc:
            if is_class:
                raise RuntimeError("could not execute class %s: %r" % (node.name, exc)) from exc
            continue  # a constant that depends on something not extracted: ignore
    for name in ("TurnAssembler", "TurnResult", "CHUNK_SAMPLES", "SAMPLE_RATE"):
        if name not in ns:
            raise RuntimeError("%s not found in %s" % (name, path))
    return ns


try:
    NS = load_units(SOURCE)
except Exception as exc:  # report clearly instead of a traceback
    print("SETUP FAILED: %r" % (exc,))
    print("RESULT SETUP FAILED (0/0)")
    sys.exit(2)

TurnAssembler = NS["TurnAssembler"]
CHUNK_SAMPLES = NS["CHUNK_SAMPLES"]
SAMPLE_RATE = NS["SAMPLE_RATE"]
CHUNK_MS = CHUNK_SAMPLES * 1000.0 / SAMPLE_RATE
MERGE, MINSP, HANG = 800, 250, 100
GAP_LIMIT = max(0, int(round(MERGE / CHUNK_MS)))  # mirrors the class's own rounding


def chunks_for_speech(ms: float) -> int:
    """Smallest piece length (in chunks) whose estimated speech is at least ms."""
    return int(math.ceil((ms + HANG) / CHUNK_MS))


class Driver:
    def __init__(self, merge=MERGE, minsp=MINSP, hang=HANG):
        self.a = TurnAssembler(merge_gap_ms=merge, min_speech_ms=minsp, hangover_ms=hang)
        self.results = []
        self.last_start = None
        self.last_end = None

    def step(self, event=None, elapsed=0.0):
        chunk = np.zeros(CHUNK_SAMPLES, dtype=np.int16)
        if self.a.collecting:
            self.a.chunks.append(chunk)
        r = self.a.tick()
        if r is not None:
            self.results.append(r)
        if event == "start":
            self.last_start = self.a.start_piece(chunk)
        elif event == "end":
            self.last_end = self.a.end_piece(elapsed)
        return r

    def quiet(self, n):
        for _ in range(n):
            self.step()

    def piece(self, p, elapsed=0.0):
        assert p >= 2
        self.step("start")
        self.quiet(p - 2)
        self.step("end", elapsed)

    def release(self):
        for i in range(1, GAP_LIMIT + 5):
            if self.step() is not None:
                return i
        raise AssertionError("held turn was never released")


class TurnAssemblerTests(unittest.TestCase):
    def test_single_piece_released_after_merge_window(self):
        d = Driver()
        p = chunks_for_speech(1000)
        d.piece(p, elapsed=12.5)
        self.assertAlmostEqual(d.last_end[0], p * CHUNK_MS / 1000.0, places=6)
        self.assertAlmostEqual(d.last_end[1], p * CHUNK_MS - HANG, places=6)
        self.assertTrue(d.a.collecting, "held turn must keep collecting")
        self.assertEqual(d.results, [])
        self.assertEqual(d.release(), GAP_LIMIT)
        r = d.results[0]
        self.assertEqual(r.pieces, 1)
        self.assertTrue(r.accepted)
        self.assertAlmostEqual(r.speech_ms, p * CHUNK_MS - HANG, places=6)
        self.assertEqual(len(r.chunks), p, "waiting-window silence must be dropped")
        self.assertEqual(r.end_elapsed, 12.5)
        self.assertFalse(d.a.collecting)

    def test_no_release_inside_window(self):
        d = Driver()
        d.piece(chunks_for_speech(1000))
        for _ in range(GAP_LIMIT - 1):
            self.assertIsNone(d.step())
        self.assertIsNotNone(d.step())

    def test_merge_inside_window(self):
        self.assertGreaterEqual(GAP_LIMIT, 5, "test parameters invalid for this chunk size")
        d = Driver()
        pa, pb, g = chunks_for_speech(600), chunks_for_speech(900), GAP_LIMIT - 3
        d.piece(pa, elapsed=10.0)
        self.assertFalse(d.last_start)
        d.quiet(g)
        self.assertEqual(d.results, [])
        d.piece(pb, elapsed=20.0)
        self.assertTrue(d.last_start, "start inside the window must continue the held turn")
        d.release()
        self.assertEqual(len(d.results), 1)
        r = d.results[0]
        self.assertEqual(r.pieces, 2)
        self.assertTrue(r.accepted)
        self.assertAlmostEqual(r.speech_ms, (pa * CHUNK_MS - HANG) + (pb * CHUNK_MS - HANG), places=6)
        self.assertEqual(len(r.chunks), pa + g + pb, "real pause audio must be kept, trailing wait dropped")
        self.assertEqual(r.end_elapsed, 20.0)

    def test_separate_turns_when_gap_exceeds_window(self):
        d = Driver()
        pa, pb = chunks_for_speech(600), chunks_for_speech(700)
        d.piece(pa)
        d.release()
        d.piece(pb)
        self.assertFalse(d.last_start, "start after release must open a new turn")
        d.release()
        self.assertEqual(len(d.results), 2)
        self.assertEqual([r.pieces for r in d.results], [1, 1])
        self.assertEqual([len(r.chunks) for r in d.results], [pa, pb])

    def test_burst_rejected_and_boundary_accepted(self):
        ok = chunks_for_speech(MINSP)
        rej = ok - 1
        self.assertLess(rej * CHUNK_MS - HANG, MINSP, "test parameters invalid for this chunk size")
        self.assertGreaterEqual(ok * CHUNK_MS - HANG, MINSP)
        d1, d2 = Driver(), Driver()
        d1.piece(rej)
        d1.release()
        d2.piece(ok)
        d2.release()
        self.assertFalse(d1.results[0].accepted)
        self.assertTrue(d2.results[0].accepted)

    def test_short_pieces_accepted_only_when_merged(self):
        p = chunks_for_speech(MINSP * 0.6)
        each = p * CHUNK_MS - HANG
        self.assertLess(each, MINSP)
        self.assertGreaterEqual(2 * each, MINSP, "test parameters invalid for this chunk size")
        alone = Driver()
        alone.piece(p)
        alone.release()
        self.assertFalse(alone.results[0].accepted)
        d = Driver()
        d.piece(p)
        d.quiet(2)
        d.piece(p)
        d.release()
        self.assertEqual(d.results[0].pieces, 2)
        self.assertTrue(d.results[0].accepted)

    def test_min_speech_zero_disables_rejection(self):
        d = Driver(minsp=0)
        d.piece(2)
        d.release()
        self.assertTrue(d.results[0].accepted)

    def test_merge_gap_zero_disables_merging(self):
        d = Driver(merge=0)
        pa, pb = chunks_for_speech(600), chunks_for_speech(700)
        d.piece(pa)
        self.assertEqual(d.release(), 1)
        self.assertEqual(len(d.results[0].chunks), pa)
        d.piece(pb)
        self.assertFalse(d.last_start)
        d.release()
        self.assertEqual(len(d.results), 2)
        self.assertEqual([r.pieces for r in d.results], [1, 1])

    def test_collecting_lifecycle(self):
        d = Driver()
        self.assertFalse(d.a.collecting)
        d.step("start")
        self.assertTrue(d.a.collecting)
        d.quiet(5)
        d.step("end")
        self.assertTrue(d.a.collecting, "hold state must keep collecting")
        d.release()
        self.assertFalse(d.a.collecting)

    def test_speech_ms_so_far_is_conservative(self):
        d = Driver()
        d.step("start")
        d.quiet(3)  # 4 chunks open
        self.assertAlmostEqual(d.a.speech_ms_so_far(), max(0.0, 4 * CHUNK_MS - HANG), places=6)
        d2 = Driver()
        d2.step("start")
        d2.quiet(19)  # 20 chunks open
        self.assertAlmostEqual(d2.a.speech_ms_so_far(), 20 * CHUNK_MS - HANG, places=6)
        d3 = Driver()
        pa = chunks_for_speech(600)
        d3.piece(pa)
        d3.quiet(2)
        d3.step("start")
        d3.quiet(4)  # second piece open with 5 chunks
        expected = (pa * CHUNK_MS - HANG) + (5 * CHUNK_MS - HANG)
        self.assertAlmostEqual(d3.a.speech_ms_so_far(), expected, places=6)

    def test_end_piece_outside_a_piece_is_ignored(self):
        a = TurnAssembler(merge_gap_ms=MERGE, min_speech_ms=MINSP, hangover_ms=HANG)
        self.assertEqual(a.end_piece(1.0), (0.0, 0.0))
        self.assertFalse(a.collecting)
        d = Driver()
        p = chunks_for_speech(800)
        d.piece(p, elapsed=5.0)
        self.assertEqual(d.a.end_piece(99.0), (0.0, 0.0), "second end in hold state must be ignored")
        d.release()
        r = d.results[0]
        self.assertEqual(r.end_elapsed, 5.0)
        self.assertAlmostEqual(r.speech_ms, p * CHUNK_MS - HANG, places=6)


if __name__ == "__main__":
    print("Source: %s | chunk %.3f ms | merge %d ms (gap limit %d chunks) | min speech %d ms | hangover %d ms"
          % (SOURCE, CHUNK_MS, MERGE, GAP_LIMIT, MINSP, HANG))
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TurnAssemblerTests)
    res = unittest.TextTestRunner(stream=sys.stdout, verbosity=2).run(suite)
    bad = len(res.failures) + len(res.errors)
    if bad == 0:
        print("RESULT ALL PASS (%d/%d)" % (res.testsRun, res.testsRun))
    else:
        print("RESULT FAIL (%d failed of %d)" % (bad, res.testsRun))
    sys.exit(0 if bad == 0 else 1)
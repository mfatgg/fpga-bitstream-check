"""Tests for memcheck / memsearch on synthetic data (no third-party bitstream)."""

import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import memcheck as M  # noqa: E402
import memsearch as S  # noqa: E402
import layout as L  # noqa: E402
import corpus  # noqa: E402


def small_function(rng: random.Random, k: int) -> int:
    """Random 64-entry table depending on exactly k of the 6 address bits (k >= 1)."""
    while True:
        bits = rng.sample(range(6), k)
        sub = rng.getrandbits(1 << k)
        t = 0
        for a in range(64):
            idx = sum((a >> b & 1) << i for i, b in enumerate(bits))
            t |= (sub >> idx & 1) << a
        if M.support(t) == k:
            return t


def synthetic_clbs(n: int, seed: int) -> list[tuple[int, ...]]:
    """CLBs whose 4 slots hold small LUTs stored with the model layout (groups + row vectors)."""
    rng = random.Random(seed)
    slots, bases = S.model_layout()
    perms = {b: S.row_perm(bases[b]) for b in S.BANDS}
    out = []
    for _ in range(n):
        vals = [0] * 16
        for s, groups in enumerate(slots):
            t = small_function(rng, rng.choice((1, 2, 3, 4)))
            for q, g in enumerate(groups):
                perm = perms[S.GROUPS[g][1]]
                for a in range(16):
                    if t >> (16 * q + a) & 1:
                        vals[g] |= 1 << perm[a]
        out.append(tuple(vals))
    return out


class SupportTest(unittest.TestCase):
    def test_support_counts_dependent_bits(self):
        self.assertEqual(M.support(0), 0)
        self.assertEqual(M.support((1 << 64) - 1), 0)
        x0 = sum(1 << a for a in range(64) if a & 1)
        self.assertEqual(M.support(x0), 1)
        x0x5 = sum(1 << a for a in range(64) if (a & 1) ^ (a >> 5 & 1))
        self.assertEqual(M.support(x0x5), 2)

    def test_rank_and_row_perm(self):
        self.assertTrue(S.rank4((8, 4, 3, 1)))
        self.assertFalse(S.rank4((8, 4, 12, 1)))
        self.assertEqual(sorted(S.row_perm((8, 4, 3, 1))), list(range(16)))


class LayoutTest(unittest.TestCase):
    def test_memory_slots_partition_the_256_cells(self):
        cells = [c for s in range(4) for c in L.memory_lut_cells(6, 1, s)]
        self.assertEqual(len(set(cells)), 256)
        self.assertEqual({w for _, w, _ in cells}, {0, 1, 9, 10})

    def test_pins_and_offset_permute_the_same_cells(self):
        base = set(L.memory_lut_cells(22, 25, 2))
        self.assertEqual(set(L.memory_lut_cells(22, 25, 2, pins=(5, 3, 1, 0, 2, 4), offset=37)), base)
        self.assertEqual(set(L.logic_lut_cells(4, 1, 1, pins=(1, 0, 3, 2, 5, 4), offset=6)),
                         set(L.logic_lut_cells(4, 1, 1)))

    def test_logic_layout_rejects_memory_columns(self):
        with self.assertRaises(ValueError):
            L.logic_lut_cells(8, 1, 0)

    def test_read_lut_roundtrip(self):
        cells = L.lut_cells(10, 7, 3)
        fab = bytearray(corpus.FABRIC_BYTES)
        for i, (row, w, b) in enumerate(cells):
            if 0xDEADBEEF12345678 >> i & 1:
                k = (row * L.WORDS + w) * 32 + b
                fab[k >> 3] |= 1 << (k & 7)
        self.assertEqual(L.read_lut(bytes(fab), cells), 0xDEADBEEF12345678)


class LoaderTest(unittest.TestCase):
    def test_fabric_of_containers(self):
        fab = bytes(range(256)) * (corpus.FABRIC_BYTES // 256)
        mcu = bytes(1280) + bytes(40) + fab + bytes(corpus.MCU_SIZE - 1320 - corpus.FABRIC_BYTES)
        self.assertEqual(corpus.fabric_of(mcu), fab)
        self.assertIsNone(corpus.fabric_of(b"\x01" + mcu[1:]))
        self.assertEqual(corpus.fabric_of(bytes(60) + fab), fab)
        self.assertIsNone(corpus.fabric_of(b"short"))


class RecoveryTest(unittest.TestCase):
    def test_exact_partition_recovers_model_from_synthetic_luts(self):
        scorer = S.Scorer(synthetic_clbs(40, seed=3))
        slots, bases = S.model_layout()
        score, part = S.exact_partition(scorer, bases)
        self.assertTrue(S.same_partition(part, slots))
        self.assertEqual(score, scorer.score(slots, bases))

    def test_wrong_layout_scores_lower(self):
        scorer = S.Scorer(synthetic_clbs(40, seed=4))
        slots, bases = S.model_layout()
        mixed = [list(s) for s in slots]
        mixed[0][3], mixed[2][3] = mixed[2][3], mixed[0][3]
        self.assertLess(scorer.score(mixed, bases), scorer.score(slots, bases))


if __name__ == "__main__":
    unittest.main()

import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flowfuzz import CHECKER_SELFTEST, LUT_BITNUMS, design, expr, invert_lut, lut_cells


class Tests(unittest.TestCase):
    def test_design_is_deterministic(self):
        self.assertEqual(design(7), design(7))
        self.assertNotEqual(design(7), design(8))

    def test_design_has_all_ports(self):
        v = design(3)
        for k in range(8):
            self.assertIn(f"input wire i{k}", v)
            self.assertIn(f"output wire o{k}", v)

    def test_expr_leaf_at_depth_zero(self):
        self.assertIn(expr(random.Random(1), 0), ("a", "b", "{a, b}", "{b, a}") + tuple(
            f"8'd{n}" for n in range(256)))

    def test_top_level_is_never_a_leaf(self):
        leaves = ("a", "b", "{a, b}", "{b, a}")
        for seed in range(300):
            r = design(seed).split("wire [7:0] r = ")[1].split(";")[0]
            self.assertNotIn(r, leaves)
            self.assertFalse(r.startswith("8'd"))

    def _tile(self, lut0: str) -> str:
        rows = [["0"] * 54 for _ in range(16)]
        for k, b in enumerate(LUT_BITNUMS):
            rows[b // 10][36 + b % 10] = lut0[k]
        return ".logic_tile 1 1\n" + "\n".join("".join(r) for r in rows) + "\n"

    def test_lut_cells_finds_only_nonconstant_tables(self):
        self.assertEqual(lut_cells(self._tile("0110" * 4)), [(0, 0)])
        self.assertEqual(lut_cells(self._tile("1" * 16)), [])

    def test_invert_lut_flips_all_16_bits_and_nothing_else(self):
        asc = self._tile("0110" * 4)
        out = invert_lut(asc, 0, 0)
        self.assertEqual(out, self._tile("1001" * 4))
        self.assertEqual(sum(a != b for a, b in zip(asc, out)), 16)

    def test_selftest_pair_differs(self):
        a, b = CHECKER_SELFTEST
        self.assertNotEqual(a, b)


if __name__ == "__main__":
    unittest.main()

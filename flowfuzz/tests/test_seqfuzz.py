import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flowfuzz import LUT_BITNUMS
from seqfuzz import clear_bit, routing_bits, seq_design


def tile(lut0: str, routing: dict[tuple[int, int], str]) -> str:
    rows = [["0"] * 54 for _ in range(16)]
    for k, b in enumerate(LUT_BITNUMS):
        rows[b // 10][36 + b % 10] = lut0[k]
    for (r, c), v in routing.items():
        rows[r][c] = v
    return ".logic_tile 1 1\n" + "\n".join("".join(r) for r in rows) + "\n"


class Tests(unittest.TestCase):
    def test_seq_design_is_deterministic_and_observes_q(self):
        self.assertEqual(seq_design(3), seq_design(3))
        self.assertIn("{r[7:4], q}", seq_design(3))
        self.assertIn("always @(posedge clk) q <= n[3:0];", seq_design(3))

    def test_flip_only_changes_the_output_line(self):
        a, b = seq_design(5).splitlines(), seq_design(5, flip_bit=2).splitlines()
        self.assertEqual([x for x, y in zip(a, b) if x != y], [a[-2]])
        self.assertIn("8'd4", b[-2])

    def test_routing_bits_only_in_lut_tiles_and_before_lut_columns(self):
        asc = tile("0110" * 4, {(3, 5): "1", (4, 40): "1"})
        self.assertIn((1 + 3, 5), routing_bits(asc))
        self.assertTrue(all(c < 36 for _, c in routing_bits(asc)))
        self.assertEqual(routing_bits(tile("0" * 16, {(3, 5): "1"})), [])

    def test_clear_bit(self):
        asc = tile("0110" * 4, {(3, 5): "1"})
        self.assertEqual(clear_bit(asc, 4, 5).splitlines()[4][5], "0")


if __name__ == "__main__":
    unittest.main()

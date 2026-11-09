import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from carrymut import carry_bits, flip


def tile(row1: str) -> str:
    rows = ["0" * 54 for _ in range(16)]
    rows[1] = row1
    return ".logic_tile 2 3\n" + "\n".join(rows) + "\n"


class Tests(unittest.TestCase):
    def test_carry_bits_are_row1_cols_49_50(self):
        r1 = "0" * 49 + "11" + "0" * 3
        self.assertEqual(carry_bits(tile(r1)), [(2, 49), (2, 50)])
        self.assertEqual(carry_bits(tile("0" * 54)), [])

    def test_flip_changes_one_bit(self):
        asc = tile("0" * 50 + "1" + "0" * 3)
        out = flip(asc, 2, 50)
        self.assertEqual(sum(a != b for a, b in zip(asc, out)), 1)


if __name__ == "__main__":
    unittest.main()

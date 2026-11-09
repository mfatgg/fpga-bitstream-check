"""Pure parts of unpack_full.py. Skipped when the venv with Apycula is not installed."""
import os
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
try:
    import unpack_full
    from unpack_full import (alu_terms, const_of, decode_alonenode_pips, join_nodes, long_wire_nodes, lut_init,
                             prune_inputs, unused_inputs)
except ImportError:                                   # apycula only lives in .venv
    alu_terms = None


@unittest.skipIf(alu_terms is None, "needs gowinfuzz/.venv (apycula)")
class Tests(unittest.TestCase):
    def test_lut_init_inverts_flags(self):
        self.assertEqual(lut_init(set()), 0xFFFF)
        self.assertEqual(lut_init({0, 15}), 0x7FFE)

    def test_long_wire_nodes_follow_the_nextpnr_rule(self):
        seg = {(0, 5, 3): {"min_x": 4, "max_x": 6, "min_y": 0, "max_y": 1, "top_row": 0, "bottom_row": 1,
                           "top_wire": "LT02", "bottom_wire": "LT20"}}
        nodes = long_wire_nodes(seg)
        self.assertIn([(0, 5, "LBO0"), (0, 4, "LB31"), (0, 5, "LB31"), (0, 6, "LB31")], nodes)
        self.assertIn([(0, 5, "LT02"), (1, 5, "LT20"), (0, 5, "LT01"), (1, 5, "LT01")], nodes)

    def test_join_nodes_drives_readers_from_the_single_driver(self):
        v = "module top();\nassign R1C6_LBO0 = R1C6_LT01;\nassign R1C5_W23 = R1C5_LB31;\nendmodule\n"
        out, st = join_nodes(v, [[(0, 5, "LBO0"), (0, 4, "LB31")]])
        self.assertIn("assign R1C5_LB31 = R1C6_LBO0;", out)
        self.assertEqual(st["assigns"], 1)

    def test_join_nodes_skips_multi_driver_nodes(self):
        v = "module top();\nassign R1C1_A = x;\nassign R1C2_B = y;\nendmodule\n"
        out, st = join_nodes(v, [[(0, 0, "A"), (0, 1, "B")]])
        self.assertEqual((out, st["multi_driver_nodes"]), (v, 1))

    def test_alu_add_mode_is_a_full_adder(self):
        p, g = alu_terms(0b0110000001101010)          # ADD, with C = 1
        for a in (0, 1):
            for b in (0, 1):
                i = a | b << 1 | 1 << 2
                self.assertEqual((p[i], g[i]), (a ^ b, a))

    def test_unused_inputs_with_constants(self):
        self.assertEqual(unused_inputs(0x303F, {"C": 1}), ["A", "D"])
        self.assertEqual(unused_inputs(0x2222, alu=False), ["C", "D"])
        self.assertEqual(unused_inputs(0x6996, alu=False), [])

    def test_const_of_follows_assign_chains(self):
        self.assertEqual(const_of("x", {"x": "y", "y": "VCC"}), 1)
        self.assertIsNone(const_of("x", {"x": "y"}))

    def test_alonenode_pips_mirror_gowin_pack(self):
        class T:                                   # SEL7 <- X01, pack adds alone-node fuse (4,52)
            pips = {"SEL7": {"X01": {(4, 50), (6, 48)}, "X03": {(6, 48)}, "VCC": set()}}
            alonenode = {"SEL7": [({"X03", "VCC"}, {(4, 52)})]}
        tile = [[0] * 60 for _ in range(8)]
        for r, c in ((4, 50), (6, 48), (4, 52)):
            tile[r][c] = 1
        pips = {}
        decode_alonenode_pips(T, tile, pips)
        self.assertEqual(pips, {"SEL7": "X01"})
        pips = {"SEL7": "already"}                 # decoded by gowin_unpack: left alone
        decode_alonenode_pips(T, tile, pips)
        self.assertEqual(pips, {"SEL7": "already"})

    def test_prune_rewrites_zero_propagate_alus_exactly(self):
        v = ("module top();\nALU_RAW R1C1_ALU_0 (\n.A(a),\n.B(b),\n.C(c),\n.D(d),\n.SUM(s),\n.CIN(ci),\n"
             ".COUT(co)\n);\ndefparam R1C1_ALU_0.INIT = 16'h0000;\nendmodule\n")
        out, _ = prune_inputs(v)
        self.assertIn("ALU_RAW_P0 #(.INIT(16'h0000)) R1C1_ALU_0 (.A(1'b0), .B(1'b0), .COUT(co));", out)
        self.assertIn("assign s = ci;", out)


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(alu_terms is None, "needs gowinfuzz/.venv (apycula)")
class SpineRuleTests(unittest.TestCase):
    """The tap-column rule (apicula#586): GW1N-9C db tile (19,31) has GT00 <- SPINE24..27 with one
    fuse set; chipdb connects only SPINE24 there."""

    @classmethod
    def setUpClass(cls):
        cls.db = unpack_full.chipdb.load_chipdb(os.path.join(os.path.dirname(unpack_full.apycula.__file__),
                                                             "GW1N-9C.msgpack.xz"))

    def test_rule_picks_the_physical_spine_even_if_another_is_driven(self):
        v = "assign R20C32_SPINE27 = R1C1_X;\nassign R20C32_GT00 = R20C32_SPINE27;\n"
        out, by_rule, by_assumption = unpack_full.resolve_ambiguous_taps(v, self.db)
        self.assertIn("assign R20C32_GT00 = R20C32_SPINE24;  // SPINE-RULE", out)
        self.assertEqual((by_rule, by_assumption), (1, 0))

    def test_correct_choice_is_left_alone(self):
        v = "assign R20C32_GT00 = R20C32_SPINE24;\n"
        self.assertEqual(unpack_full.resolve_ambiguous_taps(v, self.db), (v, 0, 0))

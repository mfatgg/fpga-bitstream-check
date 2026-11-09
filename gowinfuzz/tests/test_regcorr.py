"""Pure parts of regcorr.py (register/ALU cut points for bitstream-vs-synthesis equivalence)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import regcorr

GOLD = """module top(clk, y);
  input clk;
  output y;
  DFFE \\cnt_DFF (
    .CE(en),
    .CLK(clk),
    .D(n1),
    .Q(q1)
  );
  ALU #(
    .ALU_MODE(32'd2)
  ) \\add0 (
    .CIN(c0),
    .COUT(c1),
    .I0(q1),
    .SUM(n1)
  );
endmodule
"""
GATE = """module top(R1C1_IA);
input R1C1_IA;
DFFE R3C5_DFFE_2 (
.CLK(R3C5_CLK1),
.D(R3C5_F2),
.Q(R3C5_Q2),
.CE(R3C5_CE1)
);
ALU_RAW R3C5_ALU_2 (.A(R3C5_A2), .CIN(R3C5_CIN2), .SUM(R3C5_F2), .COUT(R3C5_CIN3));
endmodule
"""


class Tests(unittest.TestCase):
    def test_bel_maps_to_unpack_instance(self):
        self.assertEqual(regcorr.bel_to_unpacked("X4Y2/DFF2"), "R3C5_DFFE_2")

    def test_parse_ffs_both_formats(self):
        self.assertEqual(regcorr.parse_ffs(GOLD)["cnt_DFF"].conns["D"], "n1")
        self.assertEqual(regcorr.parse_ffs(GATE)["R3C5_DFFE_2"].conns["CE"], "R3C5_CE1")

    def test_cut_replaces_ff_by_ports_and_neutralises_absent_pins(self):
        text, decls, missing = regcorr.cut(GOLD, {"cnt_DFF": "ff0"}, {"ff0": {"CE", "RESET"}})
        self.assertNotIn("DFFE", text)
        self.assertIn("assign q1 = q_ff0;", text)
        self.assertIn("assign d_ff0 = n1 ;", text)
        self.assertIn("assign reset_ff0 = 1'b0;", text)      # gate has a reset the source lacks
        self.assertEqual(missing, [])
        self.assertIn("output reset_ff0;", decls)

    def test_cut_reports_missing_instances(self):
        self.assertEqual(regcorr.cut(GOLD, {"nope": "ff0"})[2], ["nope"])

    def test_alu_outputs_become_cut_points_on_both_formats(self):
        g, gd = regcorr.cut_alu_outputs(GOLD, {"add0": ("a0", ["SUM", "COUT"])})
        self.assertIn(".SUM(o_sum_a0)", g)
        self.assertIn("assign n1 = c_sum_a0;", g)
        t, td = regcorr.cut_alu_outputs(GATE, {"R3C5_ALU_2": ("a0", ["SUM", "COUT"])})
        self.assertIn(".COUT(o_cout_a0)", t)
        self.assertIn("assign R3C5_CIN3 = c_cout_a0;", t)
        self.assertEqual(sorted(gd), sorted(td))               # identical port lists on both sides

    def test_add_ports_keeps_only_cut_ports(self):
        out = regcorr.add_ports(GATE, ["input q_ff0;", "output d_ff0;"])
        self.assertTrue(out.startswith("module top(q_ff0, d_ff0);"))
        self.assertIn("wire R1C1_IA;", out)                    # old pad port is now internal


if __name__ == "__main__":
    unittest.main()


class EscapedNames(unittest.TestCase):
    def test_escaped_identifier_on_the_right_keeps_its_terminating_space(self):
        g = "module top(a);\n  DFFE \\r  (\n    .CE(\\x.y ),\n    .CLK(c),\n    .D(d),\n    .Q(q)\n  );\nendmodule\n"
        text, _, _ = regcorr.cut(g, {"r": "ff0"})
        self.assertIn("assign ce_ff0 = \\x.y ;", text)

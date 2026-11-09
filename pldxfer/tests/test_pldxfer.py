import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pldxfer import Pal, Term, complete_proof, make_pals, mutate, ports, sop, verilog


class Tests(unittest.TestCase):
    def test_sop(self):
        t = [Term((("i0", True), ("i1", False))), Term((("i2", True),))]
        self.assertEqual(sop(t), "(i0 & ~i1) | (i2)")
        self.assertEqual(sop([]), "1'b0")

    def test_mutation_changes_exactly_one_literal(self):
        for p in make_pals():
            m = mutate(p)
            a, b = ({**p.comb, **p.regs}, {**m.comb, **m.regs})
            diffs = [(o, i, j) for o in a for i, (ta, tb) in enumerate(zip(a[o], b[o]))
                     for j, (la, lb) in enumerate(zip(ta.lits, tb.lits)) if la != lb]
            self.assertEqual(len(diffs), 1)

    def test_verilog_registered(self):
        p = Pal("x", {}, {"o0": [Term((("i0", True),))]})
        v = verilog(p)
        self.assertIn("output reg o0", v)
        self.assertIn("o0 <= (i0);", v)

    def test_ports_reads_ansi_headers(self):
        ref = "module fz(input wire clk, input wire i0, output wire [3:0] o);\nendmodule\n"
        gate = "module gate (output o, input i0, input io_16_0_1, input clk);\nendmodule\n"
        self.assertEqual(ports(ref), {"clk", "i0", "o"})
        self.assertEqual(ports(gate) ^ ports(ref), {"io_16_0_1"})

    def test_complete_proof_rule(self):
        self.assertTrue(complete_proof(4, 4, 20))
        self.assertFalse(complete_proof(4, 5, 20))
        self.assertFalse(complete_proof(4, 4, 16))


if __name__ == "__main__":
    unittest.main()

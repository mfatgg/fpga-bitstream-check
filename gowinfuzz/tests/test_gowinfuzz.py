import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gowinfuzz
from gowinfuzz import alu_adapter, cst, pad_map, raw_alu_count, without_clk, wrapper

PNR = {"modules": {"top": {
    "ports": {"i0": {"bits": [10]}, "o0": {"bits": [20]}},
    "cells": {
        "ib": {"type": "IBUF", "attributes": {"NEXTPNR_BEL": "X7Y28/IOBA"}, "parameters": {},
               "connections": {"I": [10], "O": [11]}},
        "ob": {"type": "OBUF", "attributes": {"NEXTPNR_BEL": "X46Y13/IOBB"}, "parameters": {},
               "connections": {"I": [12], "O": [20]}},
        "a1": {"type": "ALU", "attributes": {}, "parameters": {"RAW_ALU_LUT": "0011"}, "connections": {}},
        "a2": {"type": "ALU", "attributes": {}, "parameters": {"ALU_MODE": "C2L"}, "connections": {}}}}}}


class Tests(unittest.TestCase):
    def test_pad_map_uses_row_col_plus_one(self):
        self.assertEqual(pad_map(PNR), {"R29C8_IA": "i0", "R14C47_OB": "o0"})

    def test_wrapper_ties_unused_inputs_and_leaves_unused_outputs_open(self):
        unpacked = "module top(R29C8_IA, R1C2_IA, R14C47_OB, R9C9_OA);\noutput R14C47_OB;\noutput R9C9_OA;\n"
        w = wrapper(unpacked, {"R29C8_IA": "i0", "R14C47_OB": "o0"})
        self.assertIn(".R29C8_IA(i0)", w)
        self.assertIn(".R1C2_IA(1'b0)", w)
        self.assertIn(".R9C9_OA()", w)

    def test_raw_alu_count(self):
        self.assertEqual(raw_alu_count(PNR), 1)

    def test_alu_adapter_only_adds_an_unused_i2(self):
        src = "x\nmodule ALU (SUM, COUT, I0, I1, I3, CIN);\ninput I3;\nassign SUM = 0;\nendmodule\nmodule Y; endmodule\n"
        out = alu_adapter(src)
        self.assertTrue(out.startswith("module ALU_APICULA (SUM, COUT, I0, I1, I2, I3, CIN);"))
        self.assertIn("input I2;", out)
        self.assertNotIn("module Y", out)

    def test_use_family_switches_device_and_pins(self):
        try:
            gowinfuzz.use_family("GW2A-18C")
            self.assertIn('IO_LOC "i0" IOT4A;', gowinfuzz.cst())
            self.assertEqual(gowinfuzz.DEVICE, "GW2A-LV18PG256C8/I7")
        finally:
            gowinfuzz.use_family("GW1N-9C")
        self.assertIn('IO_LOC "i0" 25;', gowinfuzz.cst())

    def test_a_spine_cross_check(self):
        u = "assign R20C32_GT00 = R20C32_SPINE24;  // A-SPINE: tap source resolved\n"
        pnr = {"modules": {"top": {"netnames": {"clk": {"attributes": {"ROUTING": "1;X31Y19/GT00/SPINE24;5"}}}}}}
        self.assertEqual(gowinfuzz.a_spine_vs_nextpnr(u, pnr), (1, 0))
        pnr["modules"]["top"]["netnames"]["clk"]["attributes"]["ROUTING"] = "1;X31Y19/GT00/SPINE25;5"
        self.assertEqual(gowinfuzz.a_spine_vs_nextpnr(u, pnr), (0, 1))

    def test_cst_and_clk(self):
        self.assertEqual(cst().count("IO_LOC"), 16)
        self.assertEqual(without_clk("module fz(input wire clk, input wire i0);"), "module fz(input wire i0);")


if __name__ == "__main__":
    unittest.main()

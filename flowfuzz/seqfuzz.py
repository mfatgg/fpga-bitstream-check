"""seqfuzz: sequential designs and routing-bit mutants for the iCE40 bitstream-level fuzzer.

Each design has a 4-bit state register q (power-up 0) and outputs {r[7:4], q}, where
q <= f(a, b, q) and r = g(a, b, q) are random expressions. Because every flip-flop drives an
observed output and the gate side must have no extra flip-flops (count_ffs), the bounded check
of depth BMC_DEPTH (20 > 2^4) is a complete proof (pldxfer.complete_proof).
Controls per design: output-bit flip (must fail), one inverted LUT and one cleared routing bit
in the unpacked image (each must fail, or be proven equivalent to the unmutated netlist).
Usage: python3 seqfuzz.py [count] [workdir]
"""

import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pldxfer"))
from pldxfer import BMC_DEPTH, build_src, complete_proof, count_ffs, fix_vlog, prove, run

from flowfuzz import classify_mutant, expr, invert_lut, lut_cells

LEAVES = ("a", "b", "q", "{q, a}", "{b, q}", "{a, b}")
ROUTING_COLS = range(0, 36)   # logic-tile columns before the LUT/FF config block (36..45)


def seq_design(seed: int, flip_bit: int | None = None) -> str:
    rng = random.Random(10_000 + seed)
    ins = ", ".join(f"input wire i{k}" for k in range(8))
    outs = ", ".join(f"output wire o{k}" for k in range(8))
    nxt = expr(rng, rng.randint(2, 3), top=True, leaves=LEAVES)
    comb = expr(rng, rng.randint(2, 3), top=True, leaves=LEAVES)
    out = "{r[7:4], q}" if flip_bit is None else f"({{r[7:4], q}} ^ 8'd{1 << flip_bit})"
    return (f"module fz(input wire clk, {ins}, {outs});\n"
            "    wire [3:0] a = {i3, i2, i1, i0};\n    wire [3:0] b = {i7, i6, i5, i4};\n"
            "    reg [3:0] q = 4'd0;\n"
            f"    wire [7:0] n = {nxt};\n    wire [7:0] r = {comb};\n"
            "    always @(posedge clk) q <= n[3:0];\n"
            f"    assign {{o7, o6, o5, o4, o3, o2, o1, o0}} = {out};\nendmodule\n")


def routing_bits(asc: str) -> list[tuple[int, int]]:
    """(line, column) of every set routing bit in logic tiles that hold a used LUT."""
    lines = asc.splitlines()
    tiles = {t for t, _ in lut_cells(asc)}
    return [(t + 1 + r, c) for t in sorted(tiles) for r in range(16) for c in ROUTING_COLS
            if lines[t + 1 + r][c] == "1"]


def clear_bit(asc: str, line: int, col: int) -> str:
    lines = asc.splitlines()
    lines[line] = lines[line][:col] + "0" + lines[line][col + 1:]
    return "\n".join(lines) + "\n"


def mutant(d: Path, asc: str, name: str, tag: str) -> str:
    (d / f"{name}.asc").write_text(asc)
    r = run(["icebox_vlog", "-p", str(d / "pins.pcf"), str(d / f"{name}.asc")])
    if r.returncode:
        return "unparseable"                 # icebox_vlog rejects the mutated image: also a catch
    (d / f"{name}.v").write_text(fix_vlog(r.stdout))
    return classify_mutant(d, True, f"{name}.v", tag)


def main(argv: list[str]) -> None:
    count = int(argv[0]) if argv else 10
    work = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parent / "work-seq"
    results = []
    for seed in range(count):
        t0 = time.time()
        d = work / f"sq{seed:04d}"
        b = build_src(seq_design(seed), "fz", d, seed=seed + 1)
        row = {"seed": seed}
        if "error" in b:
            row["error"] = b["error"][-160:]
        else:
            ref_ffs, gate_ffs = count_ffs(d / "ref.v"), count_ffs(d / "gate.v")
            row.update(ref_ffs=ref_ffs, gate_ffs=gate_ffs, complete=complete_proof(ref_ffs, gate_ffs, BMC_DEPTH))
            pr = prove(d / "ref.v", d / "gate.v", True, d, "eq")
            row.update(proved=pr["proved"], counterexample=pr["counterexample"])
            (d / "other.v").write_text(seq_design(seed, flip_bit=seed % 8))
            nc = prove(d / "other.v", d / "gate.v", True, d, "neg")
            row["negative_control_caught"] = nc["counterexample"] and not nc["proved"]
            asc = (d / "unpacked.asc").read_text()
            cells, bits = lut_cells(asc), routing_bits(asc)
            row["lut_mutant"] = mutant(d, invert_lut(asc, *cells[seed % len(cells)]), "lutmut", "lutmut") \
                if cells else "no_lut"
            row["routing_mutant"] = mutant(d, clear_bit(asc, *bits[(7 * seed) % len(bits)]), "routemut",
                                           "routemut") if bits else "no_bits"
        row["s"] = round(time.time() - t0, 1)
        results.append(row)
        print(json.dumps(row), flush=True)
    kinds = ("caught", "equivalent", "unparseable", "MISSED", "no_lut", "no_bits")
    summary = {"designs": count,
               "proved": sum(1 for r in results if r.get("proved")),
               "complete_proofs": sum(1 for r in results if r.get("complete")),
               "counterexamples": [r["seed"] for r in results if r.get("counterexample")],
               "errors": [r["seed"] for r in results if "error" in r],
               "negative_controls": [r.get("negative_control_caught") for r in results].count(True),
               "lut_mutants": {k: [r.get("lut_mutant") for r in results].count(k) for k in kinds},
               "routing_mutants": {k: [r.get("routing_mutant") for r in results].count(k) for k in kinds}}
    (work / "summary.json").write_text(json.dumps({"summary": summary, "results": results}, indent=1))
    print(json.dumps(summary))


if __name__ == "__main__":
    main(sys.argv[1:])

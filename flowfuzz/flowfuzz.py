"""flowfuzz: fuzz the open iCE40 flow end to end, checked on the shipped bitstream.

FUZNET (Cosgrove/Donaldson/Wickerson, FPGA '26) fuzzed Vivado P&R with random netlists and
found 2 real bugs; it could not test nextpnr because of its implicit I/O connections. We sidestep
that by checking the *final .bin*: random arithmetic/logic design -> yosys -> nextpnr-ice40 ->
icepack -> iceunpack -> icebox_vlog -> SAT equivalence against the source (complete for
combinational designs). Any failure is either a tool bug or a bug in the checker; both are
reported, never hidden.
Usage: python3 flowfuzz.py [count] [workdir]
"""

import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pldxfer"))
from pldxfer import build_src, fix_vlog, prove, run

OPS = ("+", "-", "*", "^", "&", "|")


def expr(rng: random.Random, depth: int, top: bool = False, leaves: tuple | None = None) -> str:
    """Random 8-bit expression over a = {i3..i0}, b = {i7..i4} and constants (or over `leaves`
    plus constants, e.g. with a state register for seqfuzz.py).
    The top level is never a bare leaf (a plain wire or constant tests only routing)."""
    if depth == 0 or (not top and rng.random() < 0.2):
        if leaves is None:                     # unchanged order of rng calls: same corpus as before
            return rng.choice(("a", "b", "{a, b}", f"8'd{rng.randrange(256)}", "{b, a}"))
        return rng.choice((*leaves, f"8'd{rng.randrange(256)}"))
    k = rng.random()
    x, y = expr(rng, depth - 1, leaves=leaves), expr(rng, depth - 1, leaves=leaves)
    if k < 0.6:
        return f"({x} {rng.choice(OPS)} {y})"
    if k < 0.75:
        return f"(~{x})"
    if k < 0.85:
        return f"(({x} {rng.choice(('<', '>', '=='))} {y}) ? {expr(rng, depth - 1, leaves=leaves)} : {x})"
    return f"({x} {rng.choice(('<<', '>>'))} {rng.randrange(1, 4)})"


def design(seed: int) -> str:
    rng = random.Random(seed)
    ins = ", ".join(f"input wire i{k}" for k in range(8))
    outs = ", ".join(f"output wire o{k}" for k in range(8))
    return (f"module fz(input wire clk, {ins}, {outs});\n"
            "    wire [3:0] a = {i3, i2, i1, i0};\n    wire [3:0] b = {i7, i6, i5, i4};\n"
            f"    wire [7:0] r = {expr(rng, rng.randint(2, 4), top=True)};\n"
            "    assign {o7, o6, o5, o4, o3, o2, o1, o0} = r;\nendmodule\n")


CHECKER_SELFTEST = ("(~a)", "((b - (b & {a, b})) & (({b, a} - b) | {b, a}))")  # differ; was "proved" once


def selftest(work: Path) -> bool:
    """Regression for a checker hole (miter -ignore_gold_x without undef modelling ignored every
    reference bit that is 0): a known non-equivalent pair must yield a counterexample."""
    tpl = design(0).split("wire [7:0] r = ")[0] + "wire [7:0] r = {};\n" + \
        "    assign {o7, o6, o5, o4, o3, o2, o1, o0} = r;\nendmodule\n"
    d = work / "selftest"
    b = build_src(tpl.replace("{};", CHECKER_SELFTEST[0] + ";", 1), "fz", d)
    (d / "other.v").write_text(tpl.replace("{};", CHECKER_SELFTEST[1] + ";", 1))
    nc = prove(d / "other.v", d / "gate.v", False, d, "selftest")
    return "error" not in b and nc["counterexample"] and not nc["proved"]


# LUT truth-table bit k of logic cell n in a logic tile sits at row 2n + bit // 10, column
# 36 + bit % 10 of the bit numbering below (same order as icebox.get_lutff_lut_bits).
LUT_BITNUMS = (4, 14, 15, 5, 6, 16, 17, 7, 3, 13, 12, 2, 1, 11, 10, 0)


def lut_cells(asc: str) -> list[tuple[int, int]]:
    """(line index of a logic tile header, cell index) for every LUT with a non-constant table."""
    lines, found = asc.splitlines(), []
    for i, line in enumerate(lines):
        if line.startswith(".logic_tile"):
            rows = lines[i + 1:i + 17]
            for n in range(8):
                bits = [rows[2 * n + b // 10][36 + b % 10] for b in LUT_BITNUMS]
                if "0" in bits and "1" in bits:
                    found.append((i, n))
    return found


def invert_lut(asc: str, tile_line: int, n: int) -> str:
    """Invert every truth-table bit of one LUT, so its output flips for every input."""
    lines = asc.splitlines()
    for b in LUT_BITNUMS:
        r = tile_line + 1 + 2 * n + b // 10
        c = 36 + b % 10
        row = lines[r]
        lines[r] = row[:c] + ("1" if row[c] == "0" else "0") + row[c + 1:]
    return "\n".join(lines) + "\n"


def lut_count(top_json: Path) -> int:
    j = json.loads(top_json.read_text())
    return sum(c["type"] == "SB_LUT4" for m in j["modules"].values() for c in m.get("cells", {}).values())


def bitstream_mutant(d: Path, seed: int) -> str:
    """Flip one LUT in the image recovered from the .bin; the proof against the source must now
    fail. Catches a netlist recovery that ignores parts of the bitstream.
    Returns "caught", "equivalent" (the flipped LUT is dead or masked logic: the mutated netlist
    is proven equivalent to the unmutated one, so no check could see it), "MISSED" (the mutant
    differs from the shipped netlist, yet the source proof still passed: a checker hole) or
    "no_lut"."""
    asc = (d / "unpacked.asc").read_text()
    cells = lut_cells(asc)
    if not cells:
        return "no_lut"
    (d / "mut.asc").write_text(invert_lut(asc, *cells[seed % len(cells)]))
    r = run(["icebox_vlog", "-p", str(d / "pins.pcf"), str(d / "mut.asc")])
    (d / "gate_mut.v").write_text(fix_vlog(r.stdout))
    return classify_mutant(d)


def classify_mutant(d: Path, sequential: bool = False, mut: str = "gate_mut.v", tag: str = "bitmut") -> str:
    nc = prove(d / "ref.v", d / mut, sequential, d, tag)
    if nc["counterexample"] and not nc["proved"]:
        return "caught"
    if nc.get("undriven_fanin"):               # the mutant left nets floating or made a loop:
        return "caught_undriven"               # detected as a structural change (pldxfer.prove)
    same = prove(d / "gate.v", d / mut, sequential, d, f"{tag}_vs_gate")
    return "equivalent" if same["proved"] else "MISSED"


def main(argv: list[str]) -> None:
    count = int(argv[0]) if argv else 20
    work = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parent / "work"
    if not selftest(work):
        raise SystemExit("checker self-test failed: a non-equivalent pair was not caught")
    results, trivial, seed = [], [], -1
    while len(results) < count:
        seed += 1
        t0 = time.time()
        d = work / f"fz{seed:04d}"
        b = build_src(design(seed), "fz", d, seed=seed + 1)
        row = {"seed": seed}
        if "error" in b:
            row["error"] = b["error"][-160:]
        elif lut_count(d / "top.json") == 0:  # wires/constants only: skip, but count it
            trivial.append(seed)
            continue
        else:
            row["luts"] = lut_count(d / "top.json")
            pr = prove(d / "ref.v", d / "gate.v", False, d, "eq")
            row.update(proved=pr["proved"], counterexample=pr["counterexample"])
            if True:  # negative control on every design: the same design with one output bit
                # flipped must not prove (bit seed % 8, so every output bit gets exercised)
                (d / "other.v").write_text(design(seed).replace(
                    "} = r;", f"}} = r ^ 8'd{1 << (seed % 8)};"))
                nc = prove(d / "other.v", d / "gate.v", False, d, "neg")
                row["negative_control_caught"] = nc["counterexample"] and not nc["proved"]
                row["bitstream_mutant"] = bitstream_mutant(d, seed)
        row["s"] = round(time.time() - t0, 1)
        results.append(row)
        print(json.dumps(row), flush=True)
    luts = sorted(r["luts"] for r in results if "luts" in r)
    summary = {"designs": count, "skipped_trivial": len(trivial),
               "luts_min_median_max": [luts[0], luts[len(luts) // 2], luts[-1]] if luts else None,
               "proved": sum(1 for r in results if r.get("proved")),
               "counterexamples": [r["seed"] for r in results if r.get("counterexample")],
               "errors": [r["seed"] for r in results if "error" in r],
               "negative_controls": [r["negative_control_caught"] for r in results
                                     if "negative_control_caught" in r].count(True),
               "bitstream_mutants": {k: [r.get("bitstream_mutant") for r in results].count(k)
                                     for k in ("caught", "equivalent", "MISSED", "no_lut")},
               "bitstream_mutants_missed": [r["seed"] for r in results
                                            if r.get("bitstream_mutant") == "MISSED"]}
    (work / "summary.json").write_text(json.dumps({"summary": summary, "results": results}, indent=1))
    print(json.dumps(summary))


if __name__ == "__main__":
    main(sys.argv[1:])

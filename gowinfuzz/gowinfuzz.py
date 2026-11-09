"""gowinfuzz spike: fuzz the open Gowin flow (yosys -> nextpnr-himbaechel -> Apicula), checked on
the netlist that gowin_unpack recovers from the final .fs bitstream (via unpack_full.py, which
fixes the dropped constant-1 LUTs; see README).

Same idea as ../flowfuzz (iCE40): random 8-bit combinational designs, SAT equivalence against the
source, a checker self-test, and an output-bit negative control on every design. When a design
fails, the post-synthesis netlist is proven too, to tell a synthesis bug from a P&R/pack/unpack bug.
Tools come from a local venv (.venv: Apycula, yowasp-nextpnr-himbaechel-gowin); yosys from PATH.
Usage: python3 gowinfuzz.py [count] [workdir] [alu|noalu|seq] [GW1N-9C|GW2A-18C]
       python3 gowinfuzz.py reclassify RUN.log WORKDIR
"""

import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "flowfuzz"))
from flowfuzz import CHECKER_SELFTEST, design
from seqfuzz import seq_design

VENV = HERE / ".venv" / "bin"
ALU_V = HERE / "alu_apicula.v"   # written by write_alu_adapter() at start-up
# family -> (nextpnr device, input locations, output locations). GW1N-9C uses Tang Nano 9K header
# pin numbers; GW2A-18C (Tang Primer 20K class) uses edge IO names (IO<edge><index><A|B>), which
# nextpnr's cst parser accepts on any device without the vendor pin tables.
DEVICES = {
    "GW1N-9C": ("GW1NR-LV9QN88PC6/I5", (25, 26, 27, 28, 29, 30, 33, 34), (35, 40, 41, 42, 51, 53, 54, 55)),
    # bonded, function-free top-edge pins of PBGA256, from Apicula's db.pinout (2026-11-09)
    "GW2A-18C": ("GW2A-LV18PG256C8/I7",
                 ("IOT4A", "IOT4B", "IOT5A", "IOT5B", "IOT6A", "IOT6B", "IOT8A", "IOT8B"),
                 ("IOT12A", "IOT12B", "IOT13A", "IOT13B", "IOT14A", "IOT14B", "IOT16A", "IOT16B")),
}
CLK_PINS = {"GW1N-9C": 52, "GW2A-18C": "IOT18A"}   # Tang Nano 9K clock pin; a free GW2A top pin
SEQ = False                            # sequential corpus (seqfuzz designs): set by main()
SEQ_DEPTH = 20                         # bounded check; complete when the gate keeps the 4 state bits
FAMILY = "GW1N-9C"                     # set by main() from the optional 4th argument
DEVICE, IN_PINS, OUT_PINS = DEVICES[FAMILY]


def use_family(family: str) -> None:
    global FAMILY, DEVICE, IN_PINS, OUT_PINS
    FAMILY = family
    DEVICE, IN_PINS, OUT_PINS = DEVICES[family]


STEP_TIMEOUT_S = 180   # some -noalu multiplies make synthesis run for many minutes


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run([str(c) for c in cmd], capture_output=True, text=True, timeout=STEP_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", f"timeout after {STEP_TIMEOUT_S} s")


def cst() -> str:
    lines = [f'IO_LOC "i{k}" {p};' for k, p in enumerate(IN_PINS)]
    if SEQ:
        lines.append(f'IO_LOC "clk" {CLK_PINS[FAMILY]};')
    return "\n".join(lines + [f'IO_LOC "o{k}" {p};' for k, p in enumerate(OUT_PINS)]) + "\n"


def without_clk(src: str) -> str:
    """The flowfuzz generator adds an unused clk port; drop it (no clock pin is constrained)."""
    return src.replace("input wire clk, ", "")


def pad_map(pnr: dict) -> dict[str, str]:
    """Unpacked port name (R<y+1>C<x+1>_I/O<A|B>) -> design port, from the IO BELs nextpnr chose."""
    m = pnr["modules"]["top"]
    bit2port = {p["bits"][0]: n for n, p in m["ports"].items()}
    out = {}
    for c in m["cells"].values():
        bel = re.match(r"X(\d+)Y(\d+)/IOB([AB])$", c.get("attributes", {}).get("NEXTPNR_BEL", ""))
        if not bel or c["type"] not in ("IBUF", "OBUF"):
            continue
        x, y, ab = int(bel[1]), int(bel[2]), bel[3]
        pin, kind = ("I", "I") if c["type"] == "IBUF" else ("O", "O")
        out[f"R{y + 1}C{x + 1}_{kind}{ab}"] = bit2port[c["connections"][pin][0]]
    return out


def wrapper(unpacked: str, pads: dict[str, str]) -> str:
    """Module `gate` with the design's ports around the unpacked `top`; unused pads tied to 0."""
    ports = [p.strip() for p in re.search(r"module top\((.*?)\);", unpacked, re.S)[1].split(",")]
    outs = set(re.findall(r"^output (\w+);", unpacked, re.M))
    args = [f".{p}({pads[p]})" if p in pads else (f".{p}()" if p in outs else f".{p}(1'b0)")
            for p in ports]
    io = ", ".join((["input wire clk"] if SEQ else []) + [f"input wire i{k}" for k in range(8)] +
                   [f"output wire o{k}" for k in range(8)])
    return f"module gate({io});\n  top u({', '.join(args)});\nendmodule\n"


def build(src: str, d: Path, seed: int, noalu: bool = False) -> dict:
    d.mkdir(parents=True, exist_ok=True)
    (d / "ref.v").write_text(src)
    (d / "pins.cst").write_text(cst())
    steps = [
        ["yosys", "-q", "-p", f"read_verilog {d/'ref.v'}; synth_gowin -top fz -nodsp {'-noalu ' if noalu else ''}-json {d/'top.json'}; "
                              f"write_verilog -noattr {d/'synth.v'}"],
        [VENV / "yowasp-nextpnr-himbaechel-gowin", "--json", d / "top.json", "--write", d / "pnr.json",
         "--device", DEVICE, "--vopt", f"family={FAMILY}", "--vopt", f"cst={d/'pins.cst'}",
         "--seed", seed, "-q"],
        [VENV / "gowin_pack", "-d", FAMILY, "-o", d / "top.fs", d / "pnr.json"],
        [VENV / "python", HERE / "unpack_full.py", "-d", FAMILY, "-o", d / "unpacked.v", d / "top.fs"],
    ]
    for cmd in steps:
        r = run(cmd)
        if r.returncode:
            return {"error": f"{Path(str(cmd[0])).name}: {(r.stderr or r.stdout)[-300:]}"}
    pads = pad_map(json.loads((d / "pnr.json").read_text()))
    unpacked = (d / "unpacked.v").read_text()
    (d / "unpacked.v").write_text(re.sub(r"^ALU ", "ALU_APICULA ", unpacked, flags=re.M))
    (d / "gate.v").write_text(wrapper(unpacked, pads))
    stats_file = d / "unpacked.v.stats.json"
    stats = json.loads(stats_file.read_text()) if stats_file.exists() else {}
    return {k: stats[s] for k, s in (("a_spine", "a_spine_taps_resolved"), ("spine_rule", "spine_rule_taps_resolved"))
            if stats.get(s)}


def alu_adapter(cells_sim: str) -> str:
    """yosys's Gowin ALU model, unchanged except for an extra, unused I2 input: gowin_unpack
    connects the C input of an ALU LUT as I2, which the hardware ALU modes do not use.
    (Without ALU decoding, `gowin_unpack --noalu` drops the carry chains entirely.)"""
    alu = cells_sim[cells_sim.index("module ALU ("):]
    alu = alu[:alu.index("endmodule") + len("endmodule")]
    alu = alu.replace("module ALU (SUM, COUT, I0, I1, I3, CIN);", "module ALU_APICULA (SUM, COUT, I0, I1, I2, I3, CIN);", 1)
    alu = alu.replace("input I3;", "input I3;\ninput I2;  // unused, see docstring", 1)
    return alu + "\n\n" + ALU_RAW_V


# Raw-LUT ALU as emitted by unpack_full.py (fix 3), after Apicula doc/alu.md. Checked to equal
# yosys's ALU model for all 10 named modes with their LUT patterns (2026-11-08).
ALU_RAW_V = """module ALU_RAW (input A, input B, input C, input D, input CIN, output SUM, output COUT);
    parameter [15:0] INIT = 16'h0000;
    wire P = INIT[{D, C, B, A}];   // propagate: the full LUT4
    wire G = INIT[{B, A}];         // generate: the lowest 4 bits (shared LUT2)
    assign SUM = P ^ CIN;
    assign COUT = P ? CIN : G;
endmodule

module ALU_RAW_P0 (input A, input B, output COUT);   // ALU_RAW with P == 0: COUT = G only
    parameter [15:0] INIT = 16'h0000;
    assign COUT = INIT[{B, A}];
endmodule
"""


def prove(gold: Path, gate_files: list[Path], gate_top: str, d: Path, tag: str) -> dict:
    """SAT equivalence of module fz (gold) and gate_top, with yosys's Gowin cell models."""
    # Each side is elaborated on its own (hierarchy -top), then stashed and combined. Reading both
    # into one design and flattening without that step left the parameterised Gowin cells
    # (LUT4 #(INIT), wide muxes) underived and gave FALSE counterexamples (found 2026-11-08 by
    # simulating a "failing" LUT6 netlist: iverilog and yosys eval were right, the miter was not).
    files = " ".join(str(f) for f in gate_files + [ALU_V])
    script = (f"read_verilog {files}; read_verilog +/gowin/cells_sim.v; hierarchy -top {gate_top}; "
              f"proc; flatten; opt_clean; design -stash gate; "
              f"read_verilog {gold}; hierarchy -top fz; proc; flatten; design -stash gold; "
              f"design -copy-from gold -as gold fz; design -copy-from gate -as gate {gate_top}; "
              f"miter -equiv -flatten -make_outputs gold gate miter; hierarchy -top miter; opt; "
              + (f"sat -verify -seq {SEQ_DEPTH} -prove trigger 0 -show-inputs -show-outputs"
                 if SEQ else "sat -verify -prove trigger 0 -show-inputs -show-outputs"))
    r = run(["yosys", "-p", script])
    (d / f"{tag}.sat.log").write_text(r.stdout + r.stderr)
    proved = r.returncode == 0 and "SUCCESS" in r.stdout
    return {"proved": proved, "counterexample": "FAIL" in r.stdout and "model found" in r.stdout.lower(),
            "error": None if proved or "FAIL" in r.stdout else (r.stderr or r.stdout)[-300:]}


def raw_alu_count(pnr: dict) -> int:
    """ALUs whose LUT nextpnr wrote as a raw init (RAW_ALU_LUT). gowin_unpack (Apicula 0.32) only
    recognises the fixed bit patterns of named ALU modes and decodes these as C2L/ALU_MODE 9, so a
    proof on such a design says nothing about the bitstream (an unpack limit, see README)."""
    return sum(1 for c in pnr["modules"]["top"]["cells"].values()
               if c["type"] == "ALU" and c["parameters"].get("RAW_ALU_LUT"))


def undriven_fanin(gate_files: list[Path], gate_top: str) -> list[str]:
    """Wires in the output fan-in of the recovered netlist that nothing drives. After
    opt_clean -purge only logic feeding the outputs is left, so any 'used but has no driver'
    wire there is a decode gap of gowin_unpack (e.g. long-wire taps LT -> LBO/LB), which makes a
    counterexample meaningless (2026-11-08)."""
    files = " ".join(str(f) for f in gate_files + [ALU_V])
    r = run(["yosys", "-p", f"read_verilog {files}; read_verilog -lib +/gowin/cells_sim.v; "
                            f"hierarchy -top {gate_top}; proc; flatten; opt_clean -purge; check"])
    gaps = set(re.findall(r"Wire \S+?\\(\S+) is used but has no driver", r.stdout))
    if "found logic loop" in r.stdout:   # SAT is unsound on combinational loops too (2026-11-08)
        gaps.add("<logic loop in output fan-in>")
    return sorted(gaps)


def simulate(d: Path) -> str:
    """Second oracle: iverilog over all 256 inputs. 'match', 'mismatch' (0/1 differences) or 'x'
    (the recovered netlist yields X, e.g. from undriven wires)."""
    datdir = run(["yosys-config", "--datdir"]).stdout.strip()
    vvp = d / "exh.vvp"
    tb = HERE / ("sequence_tb.v" if SEQ else "exhaustive_tb.v")
    r = run(["iverilog", "-g2012", "-o", vvp, tb, d / "ref.v", d / "unpacked.v",
             d / "gate.v", ALU_V, Path(datdir) / "gowin" / "cells_sim.v"])
    if r.returncode:
        return "sim_error"
    out = run(["vvp", "-n", vvp]).stdout
    if "mismatches=0 " in out:
        return "match"
    return "x" if "gate=" in out and "X" in out.split("gate=", 1)[1][:4].upper() else "mismatch"


def nextpnr_unconnected_cin(gaps: list[str], pnr: dict) -> list[str]:
    """Undriven ALU CINs (u.R<r>C<c>_ALU_<i>.CIN) whose ALU nextpnr itself placed without a CIN
    connection: the design then depends on the carry state at that location. That is a tool-side
    issue, e.g. nextpnr's old carry-from-logic head 0x505a (COUT = I0 | CIN), fixed upstream in
    6030081a15 (2026-09-13) but still in yowasp 0.11.1.0.post826."""
    cells = {c.get("attributes", {}).get("NEXTPNR_BEL"): c for c in pnr["modules"]["top"]["cells"].values()}
    hits = []
    for g in gaps:
        m = re.match(r"u\.R(\d+)C(\d+)_ALU_(\d)\.CIN$", g)
        if m:
            c = cells.get(f"X{int(m[2]) - 1}Y{int(m[1]) - 1}/ALU{m[3]}")
            if c is not None and "CIN" not in c["connections"]:
                hits.append(g)
    return hits


def a_spine_vs_nextpnr(unpacked: str, pnr: dict) -> tuple[int, int]:
    """Cross-check of the resolved clock taps (SPINE-RULE and ASSUMPTION A-SPINE): each must match the pip nextpnr
    routed (X<c-1>Y<r-1>/GTx0/SPINEk). Returns (matching, differing). Agreement of two tools,
    not a hardware measurement."""
    resolved = re.findall(r"^assign R\d+C\d+_(GT[01]0) = R(\d+)C(\d+)_(SPINE\d+);  // (?:A-SPINE|SPINE-RULE)", unpacked, re.M)
    pips = set()
    for net in pnr["modules"]["top"]["netnames"].values():
        r = net.get("attributes", {}).get("ROUTING", "").split(";")
        pips |= {r[i + 1] for i in range(0, len(r) - 2, 3) if r[i + 1]}
    ok = sum(f"X{int(c) - 1}Y{int(r) - 1}/{dest}/{sp}" in pips for dest, r, c, sp in resolved)
    return ok, len(resolved) - ok


def gate_ff_bits(gate_files: list[Path], gate_top: str) -> int:
    """Flip-flop bits in the output fan-in of the recovered netlist (dead tiles' FFs purged)."""
    files = " ".join(str(f) for f in gate_files + [ALU_V])
    r = run(["yosys", "-p", f"read_verilog {files}; read_verilog +/gowin/cells_sim.v; hierarchy -top {gate_top}; "
                            f"proc; flatten; opt_clean -purge; techmap; opt_clean -purge; stat"])
    table = r.stdout.rsplit("Number of cells", 1)[-1]
    return sum(int(n) for n in re.findall(r"^\s+(\d+)\s+\$_\w*DFF\w*\s*$", table, flags=re.M | re.I))


def check(src: str, d: Path, seed: int, noalu: bool = False) -> dict:
    b = build(src, d, seed, noalu)
    if "error" in b:
        return b
    row = b | prove(d / "ref.v", [d / "unpacked.v", d / "gate.v"], "gate", d, "eq")
    row["raw_alus"] = raw_alu_count(json.loads((d / "pnr.json").read_text()))
    row["sim"] = simulate(d)
    if row.get("a_spine") or row.get("spine_rule"):
        ok, bad = a_spine_vs_nextpnr((d / "unpacked.v").read_text(), json.loads((d / "pnr.json").read_text()))
        row["a_spine_vs_nextpnr"] = {"match": ok, "differ": bad}
        if bad:                                # the assumption contradicts nextpnr: no proof claimed
            row.update(proved=False, counterexample=False, a_spine_contradicted=True)
    if SEQ:                                    # complete if the gate keeps all 4 state bits (2^4 < 20)
        row["gate_ff_bits"] = gate_ff_bits([d / "unpacked.v", d / "gate.v"], "gate")
        row["complete"] = row["gate_ff_bits"] == 4 and SEQ_DEPTH > 2 ** 4
    if not row["proved"] and row["raw_alus"]:
        row["unpack_limit"] = True
    gaps = undriven_fanin([d / "unpacked.v", d / "gate.v"], "gate")
    if gaps:                                    # checked for EVERY design: with undriven fan-in the
        row["unpack_gap"] = gaps[:6]            # miter treats those nets as don't-care, so even a
        row["sat_said_proved"] = row["proved"]  # SAT "proof" is unsound (seed 2, ALU run, 2026-11-08:
        row["proved"] = False                   # proved with them at 0, failed at 1)
        row["counterexample"] = False
        tool = nextpnr_unconnected_cin(gaps, json.loads((d / "pnr.json").read_text()))
        if tool:
            row["nextpnr_cin_unconnected"] = tool
    row["oracles_agree"] = (row["proved"] == (row["sim"] == "match")) if not row.get("unpack_gap") else None
    if not row["proved"]:                       # localise: synthesis or P&R/pack/unpack?
        synth = (d / "synth.v").read_text().replace("module fz(", "module fz_synth(", 1)
        (d / "synth_renamed.v").write_text(synth)
        row["synth_proved"] = prove(d / "ref.v", [d / "synth_renamed.v"], "fz_synth", d, "synth")["proved"]
    return row


def selftest(work: Path) -> bool:
    global SEQ
    seq, SEQ = SEQ, False                       # the self-test pair is combinational
    try:
        return _selftest(work)
    finally:
        SEQ = seq


def _selftest(work: Path) -> bool:
    tpl = without_clk(design(0)).split("wire [7:0] r = ")[0] + "wire [7:0] r = {};\n" + \
        "    assign {o7, o6, o5, o4, o3, o2, o1, o0} = r;\nendmodule\n"
    d = work / "selftest"
    if "error" in build(tpl.replace("{};", CHECKER_SELFTEST[0] + ";", 1), d, 1):
        return False
    (d / "other.v").write_text(tpl.replace("{};", CHECKER_SELFTEST[1] + ";", 1))
    nc = prove(d / "other.v", [d / "unpacked.v", d / "gate.v"], "gate", d, "selftest")
    return nc["counterexample"] and not nc["proved"]


def lut_count(top_json: Path) -> int:
    j = json.loads(top_json.read_text())
    return sum(c["type"].startswith("LUT") or c["type"] == "ALU"
               for m in j["modules"].values() for c in m.get("cells", {}).values())


def write_alu_adapter() -> None:
    datdir = run(["yosys-config", "--datdir"]).stdout.strip()
    ALU_V.write_text(alu_adapter((Path(datdir) / "gowin" / "cells_sim.v").read_text()))


def summarize(results: list[dict], count: int, trivial: int, noalu: bool) -> dict:
    cells = sorted(r["cells"] for r in results if "cells" in r)
    summary = {"designs": count, "family": FAMILY, "sequential": SEQ,
               "complete_proofs": sum(1 for r in results if r.get("proved") and r.get("complete")),
               "synthesis": "-noalu" if noalu else "default (ALU carry chains)",
               "skipped_trivial": trivial,
               "unpack_limit_raw_alu": [r["seed"] for r in results if r.get("unpack_limit")],
               "cells_min_median_max": [cells[0], cells[len(cells) // 2], cells[-1]] if cells else None,
               "proved": sum(1 for r in results if r.get("proved")),
               "proved_under_assumption_a_spine": [r["seed"] for r in results if r.get("proved") and r.get("a_spine")],
               "a_spine_contradicted_by_nextpnr": [r["seed"] for r in results if r.get("a_spine_contradicted")],
               "unpack_gap_undriven": [r["seed"] for r in results if r.get("unpack_gap")
                                       and not r.get("nextpnr_cin_unconnected")],
               "nextpnr_cin_unconnected": [r["seed"] for r in results if r.get("nextpnr_cin_unconnected")],
               "counterexamples": [r["seed"] for r in results if r.get("counterexample")
                                   and not r.get("unpack_limit") and not r.get("unpack_gap")],
               "flow_errors": [r["seed"] for r in results if r.get("error") and r.get("proved") is None],
               "check_errors": [r["seed"] for r in results if r.get("error") and r.get("proved") is False
                                and not r.get("counterexample")],
               "negative_controls": [r.get("negative_control_caught") for r in results].count(True),
               "oracle_disagreements": [r["seed"] for r in results if r.get("oracles_agree") is False]}
    return summary


def reclassify(log: Path, work: Path) -> dict:
    """Apply the strict rule (undriven fan-in = unpack_gap, even if SAT said proved) to an
    existing run log, without re-running P&R. Writes <log>.reclassified.json."""
    write_alu_adapter()
    lines = log.read_text().splitlines()
    head = json.loads(lines[-1])
    results = [json.loads(x) for x in lines[:-1]]
    for r in results:
        d = work / f"gz{r['seed']:04d}"
        if r.get("proved") is None or not (d / "gate.v").exists():
            continue
        gaps = undriven_fanin([d / "unpacked.v", d / "gate.v"], "gate")
        if gaps:
            r.setdefault("sat_said_proved", r["proved"])
            r.update(unpack_gap=gaps[:6], proved=False, counterexample=False, oracles_agree=None)
    summary = summarize(results, head["designs"], head["skipped_trivial"], head["synthesis"] == "-noalu")
    (log.parent / f"{log.name}.reclassified.json").write_text(json.dumps(summary, indent=1))
    return summary


def main(argv: list[str]) -> None:
    if argv and argv[0] == "reclassify":                 # gowinfuzz.py reclassify RUN.log WORKDIR
        print(json.dumps(reclassify(Path(argv[1]), Path(argv[2]))))
        return
    count = int(argv[0]) if argv else 10
    work = Path(argv[1]) if len(argv) > 1 else HERE / "work"
    global SEQ
    noalu = len(argv) > 2 and argv[2] == "noalu"
    SEQ = len(argv) > 2 and argv[2] == "seq"
    if len(argv) > 3:
        use_family(argv[3])
    write_alu_adapter()
    if not selftest(work):
        raise SystemExit("checker self-test failed: a non-equivalent pair was not caught")
    results, trivial, seed = [], 0, -1
    while len(results) < count:
        seed += 1
        t0 = time.time()
        d = work / f"gz{seed:04d}"
        src = seq_design(seed) if SEQ else without_clk(design(seed))
        row = {"seed": seed, **check(src, d, seed + 1, noalu)}
        if "error" not in row or row.get("proved") is not None:
            if (d / "top.json").exists() and lut_count(d / "top.json") == 0:
                trivial += 1
                continue
            row["cells"] = lut_count(d / "top.json")
            if row.get("proved"):
                (d / "other.v").write_text(seq_design(seed, flip_bit=seed % 8) if SEQ else
                                           src.replace("} = r;", f"}} = r ^ 8'd{1 << (seed % 8)};"))
                nc = prove(d / "other.v", [d / "unpacked.v", d / "gate.v"], "gate", d, "neg")
                row["negative_control_caught"] = nc["counterexample"] and not nc["proved"]
        row["s"] = round(time.time() - t0, 1)
        results.append(row)
        print(json.dumps(row), flush=True)
    summary = summarize(results, count, trivial, noalu)
    (work / "summary.json").write_text(json.dumps({"summary": summary, "results": results}, indent=1))
    print(json.dumps(summary))


if __name__ == "__main__":
    main(sys.argv[1:])

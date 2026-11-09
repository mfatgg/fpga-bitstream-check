"""pldxfer: transplant PAL/GAL-style logic to an iCE40 and prove equivalence
on the *shipped bitstream*, not on the source.

Flow per design:
  reference (sum-of-products, the form a PAL/GAL JEDEC decodes to)
    -> yosys synth_ice40 -> nextpnr-ice40 -> icepack  (the image you would ship)
    -> iceunpack -> icebox_vlog                     (netlist recovered from the bitstream)
    -> yosys miter + SAT: combinational = full proof over all inputs;
                          registered   = temporal induction from the power-up state
  plus a negative control (one product term changed in the reference) that must FAIL
  with a counterexample.
Usage: python3 pldxfer.py [workdir]
"""

import json
import random
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

NI, NO = 8, 8
IN_PINS = (2, 3, 4, 6, 9, 10, 11, 12)
OUT_PINS = (13, 18, 19, 20, 21, 23, 25, 26)
CLK_PIN = 35
BMC_DEPTH = 20


@dataclass(frozen=True)
class Term:
    """One product term: literal map {signal: polarity}."""
    lits: tuple[tuple[str, bool], ...]


@dataclass(frozen=True)
class Pal:
    name: str
    comb: dict            # output name -> list[Term]   (combinational outputs)
    regs: dict            # output name -> list[Term]   (registered outputs: next state)

    def signals(self) -> list[str]:
        return [f"i{k}" for k in range(NI)] + list(self.regs)


def random_terms(rng: random.Random, signals: list[str], n_min=2, n_max=7, w_min=2, w_max=5) -> list[Term]:
    terms = []
    for _ in range(rng.randint(n_min, n_max)):
        picked = rng.sample(signals, rng.randint(w_min, w_max))
        terms.append(Term(tuple((s, rng.random() < 0.5) for s in sorted(picked))))
    return terms


def make_pals(seed: int = 56) -> list[Pal]:
    rng = random.Random(seed)
    ins = [f"i{k}" for k in range(NI)]
    gal16v8 = Pal("gal16v8_decoder", {f"o{k}": random_terms(rng, ins) for k in range(NO)}, {})
    regs = {f"o{k}": [] for k in range(4)}
    sigs = ins + list(regs)
    regs = {r: random_terms(rng, sigs, 2, 6) for r in regs}          # feedback, like PAL16R4
    comb = {f"o{k}": random_terms(rng, sigs) for k in range(4, NO)}
    pal16r4 = Pal("pal16r4_fsm", comb, regs)
    return [gal16v8, pal16r4]


def sop(terms: list[Term]) -> str:
    if not terms:
        return "1'b0"
    return " | ".join("(" + " & ".join(s if pol else f"~{s}" for s, pol in t.lits) + ")" for t in terms)


def verilog(p: Pal, module: str | None = None) -> str:
    ports = ["input wire clk"] + [f"input wire i{k}" for k in range(NI)]
    ports += [f"output {'reg' if f'o{k}' in p.regs else 'wire'} o{k}" for k in range(NO)]
    body = [f"    assign {o} = {sop(t)};" for o, t in p.comb.items()]
    for o in p.regs:
        body.append(f"    initial {o} = 1'b0;")
    if p.regs:
        body.append("    always @(posedge clk) begin")
        body += [f"        {o} <= {sop(t)};" for o, t in p.regs.items()]
        body.append("    end")
    return f"module {module or p.name}({', '.join(ports)});\n" + "\n".join(body) + "\nendmodule\n"


def mutate(p: Pal, seed: int = 1) -> Pal:
    """Flip the polarity of one literal in one product term (a single wrong fuse)."""
    rng = random.Random(seed)
    table = p.regs if p.regs else p.comb
    out = rng.choice(sorted(table))
    terms = list(table[out])
    i = rng.randrange(len(terms))
    lits = list(terms[i].lits)
    j = rng.randrange(len(lits))
    lits[j] = (lits[j][0], not lits[j][1])
    terms[i] = Term(tuple(lits))
    new = {**table, out: terms}
    return Pal(p.name + "_mut", new if not p.regs else p.comb, new if p.regs else p.regs)


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def build(p: Pal, work: Path) -> dict:
    return build_src(verilog(p), p.name, work / p.name)


def build_src(src: str, top: str, d: Path, seed: int = 1) -> dict:
    """Verilog with ports clk, i0..i7, o0..o7 -> shipped .bin -> netlist recovered from it."""
    d.mkdir(parents=True, exist_ok=True)
    (d / "ref.v").write_text(src)
    pcf = [f"set_io i{k} {pin}" for k, pin in enumerate(IN_PINS)]
    pcf += [f"set_io o{k} {pin}" for k, pin in enumerate(OUT_PINS)] + [f"set_io clk {CLK_PIN}"]
    (d / "pins.pcf").write_text("\n".join(pcf) + "\n")
    r = run(["yosys", "-q", "-p", f"read_verilog {d/'ref.v'}; synth_ice40 -top {top} -json {d/'top.json'}"])
    if r.returncode:
        return {"error": "yosys: " + r.stderr[-300:]}
    r = run(["nextpnr-ice40", "--up5k", "--package", "sg48", "--json", str(d / "top.json"),
             "--pcf", str(d / "pins.pcf"), "--asc", str(d / "top.asc"), "--seed", str(seed), "-q"])
    if r.returncode:
        return {"error": "nextpnr: " + r.stderr[-300:]}
    run(["icepack", str(d / "top.asc"), str(d / "top.bin")], check=True)
    # recover the netlist from the shipped image, not from the .asc nextpnr wrote
    run(["iceunpack", str(d / "top.bin"), str(d / "unpacked.asc")], check=True)
    r = run(["icebox_vlog", "-p", str(d / "pins.pcf"), str(d / "unpacked.asc")])
    if r.returncode:
        return {"error": "icebox_vlog: " + r.stderr[-300:]}
    (d / "gate.v").write_text(fix_vlog(r.stdout))
    return {"bin_bytes": (d / "top.bin").stat().st_size, "dir": str(d)}


def fix_vlog(src: str) -> str:
    """icebox_vlog quirks: rename the module, keep one declaration per name."""
    src = re.sub(r"^module\s+\w+", "module gate", src, count=1, flags=re.M)
    seen, out = set(), []
    for line in src.splitlines():
        m = re.match(r"\s*(reg|wire)\s+(\[[^\]]+\]\s*)?([\w$\\.\[\]]+)\s*(=.*)?;", line)
        if m and m.group(3) in seen and m.group(4) is None:
            continue
        if m:
            seen.add(m.group(3))
        out.append(line)
    return "\n".join(out) + "\n"


def ports(src: str) -> set[str]:
    """Port names of the first module header (ANSI style, as written by us and by icebox_vlog)."""
    header = re.search(r"\bmodule\s+\w+\s*\((.*?)\)\s*;", src, re.S)
    return set(re.findall(r"\b(?:input|output|inout)\s+(?:wire\s+|reg\s+)?(?:\[[^\]]*\]\s*)?(\w+)",
                          header[1])) if header else set()


def undriven_fanin(v: Path) -> list[str]:
    """Nets in the output fan-in that nothing drives (opt_clean -purge first removes dead logic).
    A miter treats them as don't-care, which made a Gowin "proof" unsound on 2026-11-08."""
    r = run(["yosys", "-p", f"read_verilog {v}; hierarchy -auto-top; proc; flatten; opt_clean -purge; check"])
    gaps = set(re.findall(r"Wire \S+?\\(\S+) is used but has no driver", r.stdout))
    if "found logic loop" in r.stdout:   # a combinational loop also makes the SAT verdict unsound
        gaps.add("<logic loop in output fan-in>")
    return sorted(gaps)


def prove(ref_v: Path, gate_v: Path, sequential: bool, work: Path, tag: str) -> dict:
    """Miter gold (reference) vs gate (bitstream netlist); SAT proof or counterexample.
    Fails first if the gate has undriven nets in its output fan-in, or if the port lists differ:
    miter matches ports by name and would leave an extra gate input (e.g. a bitstream that also
    listens to an unexpected pin) floating (both added 2026-11-08)."""
    undriven = undriven_fanin(gate_v)
    if undriven:     # the miter would treat these nets as don't-care: no sound proof possible
        (work / f"{tag}.sat.log").write_text(f"undriven fan-in: {undriven}\n")
        return {"proved": False, "counterexample": False, "undriven_fanin": undriven,
                "log": str(work / f"{tag}.sat.log")}
    extra = ports(gate_v.read_text()) ^ ports(ref_v.read_text())
    if extra:
        (work / f"{tag}.sat.log").write_text(f"port mismatch: {sorted(extra)}\n")
        return {"proved": False, "counterexample": True, "port_mismatch": sorted(extra),
                "log": str(work / f"{tag}.sat.log")}
    # Sequential: bounded check from the shared power-up state (all registers 0). It is a
    # complete proof when every flip-flop of both sides drives an observed output (true for
    # PAL16R4-style registered outputs) and the gate side has no extra flip-flops (checked
    # by count_ffs): the product state is then fixed by the 2^n output values, so any
    # mismatch shows up within 2^n + 1 steps. BMC_DEPTH is chosen above that.
    sat = (f"sat -verify -seq {BMC_DEPTH} -prove trigger 0 -set-init-zero -show-inputs -show-outputs"
           if sequential else "sat -verify -prove trigger 0 -show-inputs -show-outputs")
    script = (f"read_verilog {ref_v}; rename -top gold; design -stash gold; "
              f"read_verilog {gate_v}; rename -top gate; design -stash gate; "
              f"design -copy-from gold -as gold gold; design -copy-from gate -as gate gate; "
              f"proc; opt_clean; flatten; "
              f"miter -equiv -flatten -make_outputs gold gate miter; hierarchy -top miter; "
              f"proc; opt; {sat}")
    log = work / f"{tag}.sat.log"
    r = run(["yosys", "-p", script])
    log.write_text(r.stdout + r.stderr)
    text = r.stdout
    proved = r.returncode == 0 and "no model found: SUCCESS" in text
    failed = "FAIL" in text or r.returncode != 0
    return {"proved": proved and not failed, "counterexample": failed and "model found" in text.lower(),
            "log": str(log)}


def count_ffs(v: Path) -> int:
    """Flip-flop *bits* after proc/flatten: techmap first splits multi-bit $dff cells into
    single-bit $_DFF_* cells (counting cells of a 4-bit register gave 1; fixed 2026-11-08)."""
    r = run(["yosys", "-p", f"read_verilog {v}; proc; flatten; opt_clean; techmap; opt_clean; stat"])
    table = r.stdout.rsplit("Number of cells", 1)[-1]
    return sum(int(n) for n in re.findall(r"^\s+(\d+)\s+\$_?\w*dff\w*\s*$", table, flags=re.M | re.I))


def complete_proof(n_ref_ffs: int, n_gate_ffs: int, depth: int) -> bool:
    """Bounded check is complete if the gate adds no state and depth > 2^n (see prove())."""
    return n_gate_ffs == n_ref_ffs and depth > 2 ** n_ref_ffs


def main(argv: list[str]) -> None:
    work = Path(argv[0]) if argv else Path(__file__).resolve().parent / "work"
    work.mkdir(parents=True, exist_ok=True)
    results = []
    for p in make_pals():
        b = build(p, work)
        res = {"design": p.name, "terms": sum(len(t) for t in {**p.comb, **p.regs}.values()), **b}
        if "error" not in b:
            d = Path(b["dir"])
            seq = bool(p.regs)
            res["ref_ffs"], res["gate_ffs"] = count_ffs(d / "ref.v"), count_ffs(d / "gate.v")
            res["complete"] = (not seq) or complete_proof(res["ref_ffs"], res["gate_ffs"], BMC_DEPTH)
            res["proof"] = prove(d / "ref.v", d / "gate.v", seq, d, "good")
            m = mutate(p)
            (d / "ref_mut.v").write_text(verilog(m, p.name))
            res["negative_control"] = prove(d / "ref_mut.v", d / "gate.v", seq, d, "mut")
        results.append(res)
    (work / "results.json").write_text(json.dumps(results, indent=1))
    print(json.dumps(results, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])

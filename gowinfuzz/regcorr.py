"""regcorr: register-correspondence equivalence of a Gowin bitstream against its synthesized netlist.

A bounded sequential proof from reset cannot reach deep states (e.g. a 24-bit counter at 0x400000).
Instead, pair every flip-flop of the recovered netlist (unpack_full.py) with the synthesized one that
nextpnr placed at that BEL, cut both into identically named ports (q_<i> in; d_<i>, ce_<i>, ... out),
and prove the logic between registers equivalent with yosys equiv_make/equiv_simple. An unproven
output names the register whose next-state logic the bitstream computes differently.

This assumes the pairing is right (it comes from nextpnr's own placement record, NEXTPNR_BEL) and
that both sides' flip-flops have the same type and INIT; type mismatches are reported, not proven.
Usage: python3 regcorr.py SYNTH.json PNR.json UNPACKED.v WORKDIR [alus] [drop=TYPE,TYPE,...]
"""

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
FF_RE = re.compile(r"^[ \t]*(DFF\w*)[ \t]+(\\\S+|\w+)[ \t]*(?:#\(.*?\)[ \t]*)?\((.*?)\);[ \t]*\n",
                   re.M | re.S)
CONN_RE = re.compile(r"\.(\w+)\(([^()]*)\)")


@dataclass
class FF:
    kind: str
    name: str
    conns: dict[str, str]
    span: tuple[int, int]


def parse_ffs(text: str) -> dict[str, FF]:
    """Flip-flop instances of a flat netlist, by instance name (without the escape backslash)."""
    out = {}
    for m in FF_RE.finditer(text):
        name = m[2].lstrip("\\")
        out[name] = FF(m[1], name, {p: e.strip() for p, e in CONN_RE.findall(m[3])}, m.span())
    return out


def bel_to_unpacked(bel: str) -> str:
    """nextpnr BEL X<c>Y<r>/DFF<k> -> gowin_unpack instance R<r+1>C<c+1>_DFFE_<k>. gowin_unpack
    names every flip-flop ..._DFFE_<k>; its cell type (DFF, DFFRE, ...) carries the kind."""
    m = re.fullmatch(r"X(\d+)Y(\d+)/DFF(\d)", bel)
    return f"R{int(m[2]) + 1}C{int(m[1]) + 1}_DFFE_{m[3]}"


def pairs(pnr: dict) -> list[tuple[str, str, str]]:
    """(index name, synthesized cell name, unpacked instance name) for every placed flip-flop."""
    rows = []
    for name, c in sorted(pnr["modules"]["top"]["cells"].items()):
        if c["type"].startswith("DFF"):
            rows.append((f"ff{len(rows)}", name, bel_to_unpacked(c["attributes"]["NEXTPNR_BEL"])))
    return rows


# Value of a control pin that a flip-flop kind does not have (DFF has no CE: always enabled, ...).
ABSENT_PIN = {"CE": "1'b1", "RESET": "1'b0", "SET": "1'b0", "CLEAR": "1'b0", "PRESET": "1'b0"}


def cut(text: str, which: dict[str, str], pins: dict[str, set[str]] | None = None
        ) -> tuple[str, list[str], list[str]]:
    """Replace the flip-flops named in `which` (instance -> index) by ports. Returns the new text,
    the new port declarations, and the instances that were not found. `pins` (index -> pin names)
    adds pins this side's flip-flop lacks, driven with their neutral value (ABSENT_PIN)."""
    ffs = parse_ffs(text)
    missing = [n for n in which if n not in ffs]
    found = sorted((ffs[n] for n in which if n in ffs), key=lambda f: f.span[0], reverse=True)
    decls = []
    for f in found:
        i = which[f.name]
        lines = [f"  assign {f.conns['Q']} = q_{i};\n"]
        decls.append(f"input q_{i};")
        for port, expr in sorted(f.conns.items()):
            if port not in ("Q", "CLK"):
                lines.append(f"  assign {port.lower()}_{i} = {expr} ;\n")   # space ends an escaped identifier
                decls.append(f"output {port.lower()}_{i};")
        for port in sorted((pins or {}).get(i, set()) - set(f.conns)):
            lines.append(f"  assign {port.lower()}_{i} = {ABSENT_PIN[port]};\n")
            decls.append(f"output {port.lower()}_{i};")
        text = text[:f.span[0]] + "".join(lines) + text[f.span[1]:]
    return text, decls, missing


def add_ports(text: str, decls: list[str]) -> str:
    """Make the cut ports the only ports of module top: the original ports (pads on the bitstream
    side, clk/led/uart on the source side) become internal wires, since equiv_make needs identical
    port lists. Top-level outputs are therefore not compared, only every register's inputs."""
    names = [d.split()[1].rstrip(";") for d in decls]
    m = re.search(r"^module top\s*\((.*?)\);", text, re.M | re.S)
    body = re.sub(r"^(\s*)(?:input|output|inout)\b", r"\1wire", text[m.end():], flags=re.M)
    return text[:m.start()] + f"module top({', '.join(names)});\n" + "\n".join(decls) + "\n" + body


ALU_RE = re.compile(r"^[ \t]*(ALU\w*)[ \t]*(?:#\((?:[^()]|\([^()]*\))*\)[ \t]*)?(\\\S+|\w+)[ \t]*\((.*?)\);[ \t]*\n",
                    re.M | re.S)
ALU_OUTS = ("SUM", "COUT")


def parse_alus(text: str) -> dict[str, FF]:
    """ALU instances (ALU, ALU_RAW, ALU_APICULA) of a flat netlist, by instance name."""
    return {m[2].lstrip("\\"): FF(m[1], m[2].lstrip("\\"), {p: e.strip() for p, e in CONN_RE.findall(m[3])},
                                   m.span())
            for m in ALU_RE.finditer(text)}


def alu_pairs(pnr: dict, gold_alus: dict, gate_alus: dict) -> dict[str, tuple[str, str, list[str]]]:
    """Index -> (source ALU, bitstream ALU, outputs connected on both sides) for every ALU that
    synthesis created and nextpnr placed (nextpnr's own head/dummy cells have no source twin)."""
    out = {}
    for name, c in sorted(pnr["modules"]["top"]["cells"].items()):
        if c["type"] != "ALU" or name not in gold_alus:
            continue
        m = re.fullmatch(r"X(\d+)Y(\d+)/ALU(\d)", c["attributes"]["NEXTPNR_BEL"])
        inst = f"R{int(m[2]) + 1}C{int(m[1]) + 1}_ALU_{m[3]}"
        if inst in gate_alus:
            outs = [o for o in ALU_OUTS if gold_alus[name].conns.get(o) and gate_alus[inst].conns.get(o)]
            out[f"a{len(out)}"] = (name, inst, outs)
    return out


def cut_alu_outputs(text: str, which: dict[str, tuple[str, list[str]]]) -> tuple[str, list[str]]:
    """Cut points at ALU outputs: instance -> (index, outputs). The cell now drives output port
    o_<out>_<i>; its former net is driven by input port c_<out>_<i>, shared by both sides."""
    alus = parse_alus(text)
    decls = []
    for a in sorted((alus[n] for n in which if n in alus), key=lambda f: f.span[0], reverse=True):
        i, outs = which[a.name]
        block = text[a.span[0]:a.span[1]]
        extra = []
        for o in outs:
            block = re.sub(rf"\.{o}\([^()]*\)", f".{o}(o_{o.lower()}_{i})", block, count=1)
            extra.append(f"  assign {a.conns[o]} = c_{o.lower()}_{i};\n")
            decls += [f"output o_{o.lower()}_{i};", f"input c_{o.lower()}_{i};"]
        text = text[:a.span[0]] + block + "".join(extra) + text[a.span[1]:]
    return text, decls


def drop_cells(text: str, kinds: set[str]) -> tuple[str, int]:
    """Remove instances of the given cell types (e.g. block RAMs): their outputs become undriven,
    i.e. free and independent on each side, so any point fed by them stays unproven."""
    rx = re.compile(r"^[ \t]*(\w+)[ \t]*(?:#\((?:[^()]|\([^()]*\))*\)[ \t]*)?(\\\S+|\w+)[ \t]*\((.*?)\);[ \t]*\n",
                    re.M | re.S)
    names = set()
    out = []
    pos = 0
    for m in rx.finditer(text):
        if m[1] in kinds:
            out.append(text[pos:m.start()])
            pos = m.end()
            names.add(m[2].lstrip("\\"))
    out.append(text[pos:])
    text = re.sub(r"^defparam (\S+?)\.\w+ = .*;\n", lambda d: "" if d[1] in names else d[0], "".join(out), flags=re.M)
    return text, len(names)


def yosys_script(gold: Path, gate: Path, alu_v: Path) -> str:
    # Same per-side elaboration as gowinfuzz.prove (each side: hierarchy -top, then stash).
    # Undriven nets become independent free inputs per side (setundef -anyseq). Without that,
    # yosys sets them to undef, and an undef on both sides counts as equal: a vacuous "proof"
    # (found 2026-11-09 when memories were dropped on both sides).
    return (f"read_verilog {gate} {alu_v}; read_verilog +/gowin/cells_sim.v; hierarchy -top top; "
            f"proc; flatten; opt_clean; setundef -undriven -anyseq; design -stash gate; "
            f"read_verilog {gold}; read_verilog +/gowin/cells_sim.v; hierarchy -top top; "
            f"proc; flatten; opt_clean; setundef -undriven -anyseq; design -stash gold; "
            f"design -copy-from gold -as gold top; design -copy-from gate -as gate top; "
            f"equiv_make gold gate equiv; hierarchy -top equiv; opt_clean; equiv_simple; equiv_status")


def main(argv: list[str]) -> None:
    synth_json, pnr_json, unpacked_v, work = map(Path, argv[:4])
    with_alus = "alus" in argv[4:]     # also cut at every paired ALU output (local proofs)
    drop = {k for a in argv[4:] if a.startswith("drop=") for k in a[5:].split(",")}
    work.mkdir(parents=True, exist_ok=True)
    pnr = json.loads(pnr_json.read_text())
    rows = pairs(pnr)
    # any top name; simulation-only cells ($print, $scopeinfo) are not hardware
    subprocess.run(["yosys", "-q", "-p", f"read_json {synth_json}; hierarchy -auto-top; rename -top top; "
                    f"delete t:$print t:$scopeinfo; select top; write_verilog -noattr -selected {work/'synth.v'}"],
                   check=True)
    unpacked = re.sub(r"^ALU ", "ALU_APICULA ", unpacked_v.read_text(), flags=re.M)
    gffs, uffs = parse_ffs((work / "synth.v").read_text()), parse_ffs(unpacked)
    both = [(i, gffs[s], uffs[u]) for i, s, u in rows if s in gffs and u in uffs]
    # a pin the source flip-flop has but the bitstream one lacks cannot be neutralised: report it
    kind_mismatch = [(i, g.kind, t.kind) for i, g, t in both
                     if set(g.conns) - set(t.conns) - {"CLK"} or not set(t.conns) - set(g.conns) <= set(ABSENT_PIN)]
    gate_pins = {i: set(t.conns) for i, _, t in both}
    gold, gdecls, gmiss = cut((work / "synth.v").read_text(), {s: i for i, s, _ in rows}, gate_pins)
    gate, tdecls, tmiss = cut(unpacked, {u: i for i, _, u in rows})
    n_alus = 0
    if with_alus:
        ap = alu_pairs(pnr, parse_alus(gold), parse_alus(gate))
        n_alus = len(ap)
        gold, gd = cut_alu_outputs(gold, {g: (i, o) for i, (g, _, o) in ap.items()})
        gate, td = cut_alu_outputs(gate, {t: (i, o) for i, (_, t, o) in ap.items()})
        gdecls, tdecls = gdecls + gd, tdecls + td
    dropped = (0, 0)
    if drop:
        gold, ng = drop_cells(gold, drop)
        gate, nt = drop_cells(gate, drop)
        dropped = (ng, nt)
    (work / "gold.v").write_text(add_ports(gold, gdecls))
    (work / "gate.v").write_text(add_ports(gate, tdecls))
    alu_v = HERE / "alu_apicula.v"
    r = subprocess.run(["yosys", "-p", yosys_script(work / "gold.v", work / "gate.v", alu_v)],
                       capture_output=True, text=True)
    (work / "regcorr.log").write_text(r.stdout + r.stderr)
    unproven = re.findall(r"Unproven \$equiv .*?: (\S+) (\S+)", r.stdout)
    summary = {"ffs": len(rows), "alu_cut_cells": n_alus, "dropped_gold_gate": dropped, "missing_gold": gmiss, "missing_gate": tmiss, "kind_mismatch": kind_mismatch,
               "yosys_rc": r.returncode, "status": re.findall(r"Found \d+ \$equiv cells.*|Of those cells.*",
                                                              r.stdout)[-2:],
               "unproven": unproven}
    (work / "regcorr.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps({k: (v if k != "unproven" else len(v)) for k, v in summary.items()}))


if __name__ == "__main__":
    main(sys.argv[1:])

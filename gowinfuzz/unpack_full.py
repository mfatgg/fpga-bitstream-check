"""gowin_unpack with two fixes for a complete recovered netlist (Apicula 0.32, GW1N family).

1. Emit every LUT of a logic tile, including INIT 16'hFFFF ones. gowin_unpack decodes LUTs from
   inverted fuses, so a constant-1 LUT has no fuse set, looks unused, and is dropped together
   with the wide-function muxes built next to it. An unprogrammed LUT outputs 1 on the hardware,
   so adding every missing LUTn as an empty-flag bel (decoded as INIT 16'hFFFF) is faithful.
   ALU-owned LUTs are still removed afterwards by gowin_unpack's own removeLUTs().
2. Join long-wire nodes. gowin_unpack decodes the long-wire pips (e.g. LT02 <- A6, LBO0 <- LT01)
   but never connects the names one physical long wire has in different tiles, so the branch
   wires LB*1 that feed LUT inputs stay undriven. The node rule is the one nextpnr's Gowin chip
   database generator applies to db.segments (himbaechel/uarch/gowin/gowin_arch_gen.py, ISC):
   per segment, top wire + bottom wire + LT0{1+(idx//4)*3} in every row of the segment form one
   node, and per row LBO{idx//4} + LB{idx}1 in every column of the segment form another.
   For a node with exactly one driven member, every other member gets `assign member = driver`.
3. Decode ALUs packed from RAW_ALU_LUT. gowin_unpack only recognises the fixed bit patterns of
   the named ALU modes and otherwise falls back to C2L (ALU_MODE 9, SUM unconnected). Such ALUs
   are emitted as ALU_RAW #(INIT) with the 16 LUT bits read from the same fuses gowin_pack wrote.
   ALU_RAW (see gowinfuzz.alu_adapter) follows Apicula's doc/alu.md: P = INIT[{D,C,B,A}],
   G = INIT[{B,A}], SUM = P ^ CIN, COUT = P ? CIN : G. Checked against yosys's ALU model for
   all 10 named modes (consistency of two models, not a hardware measurement). Real C2L chain
   heads are emitted the same way (gowin_unpack's mode-9 form puts COUT on F and cuts the chain).
4. Inputs of ALU_RAW and LUT4 cells that the function provably ignores (given inputs tied to
   VCC/VSS) are tied to 0: nextpnr leaves unused pins on default connections, which would
   otherwise show up as undriven fan-in. An ALU_RAW whose P is 0 on every reachable input is
   rewritten exactly as COUT = G(B, A), SUM = CIN. Anything else left undriven is still a gap.
5. Decode all-zero tiles as well. gowin_unpack builds its tile list with tile_bitmap(empty=False)
   and skips tiles without any set fuse, but their default connections (an empty-fuse source
   such as E270 <- VCC) are live and used by neighbours, e.g. a constant into an edge IO tile.
   Only their routing defaults are emitted (no cells), which keeps netlists small; anything that
   would need a cell of an empty tile is still reported as undriven.
6. Decode pips whose fuses include gowin_pack's alone-node fuses (see decode_alonenode_pips).
7. Clock network: expand gowin_unpack's `define aliases into plain names, join Apicula's own
   db.nodes of type GLOBAL_CLK/TILE_CLK (e.g. a GT00 tap across its column) with the same
   one-driver rule, and declare every used-but-undeclared tile wire. Clock taps whose spine
   sources share one fuse set are resolved by the tap-column rule from chipdb (apicula#586);
   only where that rule gives no answer, to the only driven spine: ASSUMPTION A-SPINE.
Usage (venv python): unpack_full.py <gowin_unpack arguments>
"""

import json
import os
import re
import sys

import apycula
from apycula import chipdb, codegen, gowin_unpack

_parse_tile = gowin_unpack.parse_tile_
_tile2verilog = gowin_unpack.tile2verilog
RAW_ALUS: dict[tuple[int, int, int], int] = {}   # (row, col, idx) 0-based -> 16-bit ALU LUT
# ALU kinds gowin_unpack emits with a faithful ALU_MODE; everything else (C2L, ONE2C, mode 5, a
# RAW_ALU_LUT fallback) is decoded from the LUT bits instead (gowin_unpack 0.32 emits C2L as
# mode 9 with COUT on F, and no primitive at all for ONE2C or mode 5).
FAITHFUL_KINDS = {"0", "1", "2", "3", "4", "6", "7", "8", "9", "hadder"}


def lut_init(flags: set) -> int:
    """gowin_unpack's LUT decode: a set flag k means truth-table bit k is 0 (inverted fuses)."""
    return 0xFFFF - sum(1 << f for f in flags)


def parse_tile_all_luts(db, row, col, tile, *args, **kw):
    bels, pips, clock_pips = _parse_tile(db, row, col, tile, *args, **kw)
    if not any(any(r) for r in tile):             # 5. all-zero tile: default connections only;
        return {}, pips, clock_pips                # a cell output read from it stays a gap
    tiledata = db[row, col]
    decode_alonenode_pips(tiledata, tile, pips)
    for name, flags in list(bels.items()):        # 3. raw-LUT ALUs (see module docstring)
        if name.startswith("ALU") and name[3:].isdigit() and len(flags) == 1 and not flags <= FAITHFUL_KINDS:
            # both real C2L chain heads and the RAW_ALU_LUT fallback: gowin_unpack writes them as
            # mode 9 with COUT on F, which cuts the chain; the LUT bits describe either exactly
            RAW_ALUS[row, col, int(name[3:])] = lut_init(bels.get(f"LUT{name[3:]}", set()))
    for name in tiledata.bels:                    # 1. constant-1 LUTs
        if name.startswith("LUT") and name[3:].isdigit() and name not in bels:
            bels[name] = set()
    return bels, pips, clock_pips


def decode_alonenode_pips(tiledata, tile, pips: dict) -> None:
    """6. gowin_pack also sets the 'not connected to' fuses of a destination's alone-node table
    for the sources it did not pick (route(): bits |= fuses if src not in srcs). gowin_unpack
    matches pip fuses exactly, so such a mux (e.g. GW2A SEL7 <- X01 plus fuse (4,52)) decodes to
    nothing. Mirror pack's rule for every destination left undecoded."""
    alone = getattr(tiledata, "alonenode", {}) or {}
    for dest, srcs in tiledata.pips.items():
        if dest in pips or dest not in alone:
            continue
        extra_all = set().union(*(f for _, f in alone[dest])) if alone[dest] else set()
        pip_bits = set().union(*srcs.values()) | extra_all
        used = {(r, c) for r, c in pip_bits if tile[r][c] == 1}
        hits = []
        for src, bits in srcs.items():
            want = set(bits)
            for alone_srcs, fuses in alone[dest]:
                if src not in alone_srcs:
                    want |= set(fuses)
            if want == used and want:
                hits.append(src)
        if len(hits) == 1:                         # ambiguous: leave undecoded (stays a gap)
            pips[dest] = hits[0]


def tile2verilog_raw_alus(dbrow, dbcol, bels, pips, clock_pips, mod, cst, db):
    _tile2verilog(dbrow, dbcol, bels, pips, clock_pips, mod, cst, db)
    row, col = dbrow + 1, dbcol + 1
    for (r, c, idx), init in RAW_ALUS.items():
        name = f"R{row}C{col}_ALU_{idx}"
        if (r, c) != (dbrow, dbcol):
            continue
        alu = codegen.Primitive("ALU_RAW", name)
        alu.params["INIT"] = f"16'h{init:04x}"
        alu.portmap.update({p: f"R{row}C{col}_{p}{idx}" for p in "ABCD"})
        alu.portmap["SUM"] = f"R{row}C{col}_F{idx}"
        alu.portmap["CIN"] = f"R{row}C{col}_CIN{idx}"
        alu.portmap["COUT"] = f"R{row}C{col}_CIN{idx + 1}" if idx != 5 else f"R{row}C{col + 1}_CIN0"
        mod.wires.update(alu.portmap.values())
        mod.primitives[name] = alu


def long_wire_nodes(segments: dict) -> list[list[tuple[int, int, str]]]:
    """Nodes as lists of (row, col, wire), 0-based, from Apicula's db.segments."""
    nodes = []
    for (_, x, idx), seg in segments.items():
        lt = [(seg["top_row"], x, seg["top_wire"]), (seg["bottom_row"], x, seg["bottom_wire"])]
        for row in range(seg["min_y"], seg["max_y"] + 1):
            lt.append((row, x, f"LT0{1 + (idx // 4) * 3}"))
            nodes.append([(row, x, f"LBO{idx // 4}")] +
                         [(row, col, f"LB{idx}1") for col in range(seg["min_x"], seg["max_x"] + 1)])
        nodes.append(lt)
    return nodes


def driven_names(verilog: str) -> set[str]:
    driven = set(re.findall(r"^\s*assign\s+(\w+)\s*=", verilog, re.M))
    return driven | set(re.findall(r"\.(?:F|O|Q|SUM|COUT)\((\w+)\)", verilog))


def join_nodes(verilog: str, nodes: list[list[tuple[int, int, str]]]) -> tuple[str, dict]:
    """Add `assign member = driver` for members of nodes with exactly one driven member that the
    netlist mentions. Returns (netlist, stats)."""
    driven = driven_names(verilog)
    mentioned = set(re.findall(r"\bR\d+C\d+_\w+\b", verilog))
    lines, stats = [], {"joined_nodes": 0, "assigns": 0, "multi_driver_nodes": 0}
    for node in nodes:
        names = list(dict.fromkeys(f"R{r + 1}C{c + 1}_{w}" for r, c, w in node))
        drv = [n for n in names if n in driven]
        if len(drv) > 1:
            stats["multi_driver_nodes"] += 1
            continue
        if not drv:
            continue
        targets = [n for n in names if n != drv[0] and n in mentioned and n not in driven]
        if targets:
            stats["joined_nodes"] += 1
            stats["assigns"] += len(targets)
            lines += [f"assign {t} = {drv[0]};  // long-wire node" for t in targets]
            driven.update(targets)
    if not lines:
        return verilog, stats
    i = verilog.rindex("endmodule")
    return verilog[:i] + "\n".join(lines) + "\n" + verilog[i:], stats


def alu_terms(init: int) -> tuple[list[int], list[int]]:
    """P and G of ALU_RAW for all 16 inputs {D,C,B,A} (G only depends on B, A)."""
    return [init >> i & 1 for i in range(16)], [init >> (i & 3) & 1 for i in range(16)]


def unused_inputs(init: int, fixed: dict[str, int] | None = None, alu: bool = True) -> list[str]:
    """Inputs (A..D) the cell provably ignores, given inputs with known constant values (`fixed`).
    ALU cells: P = INIT[{D,C,B,A}] and G = INIT[{B,A}]; LUT4 cells: INIT[{I3,I2,I1,I0}] (A..D)."""
    fixed = fixed or {}
    p, g = alu_terms(init)
    terms = (p, g) if alu else (p,)

    def ok(i: int) -> bool:        # input vector consistent with the known constants
        return all((i >> "ABCD".index(n) & 1) == v for n, v in fixed.items())

    return [name for k, name in enumerate("ABCD") if name not in fixed and
            all(t[i] == t[i ^ (1 << k)] for t in terms for i in range(16) if ok(i) and ok(i ^ (1 << k)))]


def const_of(wire: str, drivers: dict[str, str]) -> int | None:
    for _ in range(64):
        if wire in ("VCC", "1'b1"):
            return 1
        if wire in ("VSS", "1'b0"):
            return 0
        if wire not in drivers:
            return None
        wire = drivers[wire]
    return None


def prune_inputs(verilog: str) -> tuple[str, int]:
    """Tie provably irrelevant inputs of ALU_RAW and LUT4 cells to 1'b0, so they are not reported
    as undriven fan-in (nextpnr leaves unused pins on default connections), and rewrite ALU_RAW
    cells whose P is constant 0 exactly as COUT = G(B, A), SUM = CIN (ALU_RAW_P0). Known-constant
    inputs (assign chains to VCC/VSS) are taken into account."""
    inits = dict(re.findall(r"^defparam (\w+)\.INIT = 16'h([0-9a-f]{4});", verilog, re.M))
    drivers = dict(re.findall(r"^\s*assign\s+(\w+)\s*=\s*(\w+|1'b[01])\s*;", verilog, re.M))
    ties = 0
    lut_pins = {"A": "I0", "B": "I1", "C": "I2", "D": "I3"}

    def fix(m: re.Match) -> str:
        nonlocal ties
        typ, name, body = m[1], m[2], m[3]
        if name not in inits:
            return m[0]
        init = int(inits[name], 16)
        pins = {n: n for n in "ABCD"} if typ == "ALU_RAW" else lut_pins
        ports = dict(re.findall(r"\.(\w+)\(([^)]*)\)", body))
        fixed = {n: v for n, pin in pins.items() if pin in ports
                 for v in [const_of(ports[pin], drivers)] if v is not None}
        if typ == "ALU_RAW" and not any(alu_terms(init)[0][i] for i in range(16)
                                        if all((i >> "ABCD".index(n) & 1) == v for n, v in fixed.items())):
            ties += 1                          # P == 0 on every reachable input: exact rewrite
            g = alu_terms(init)[1]             # COUT = G(B, A): tie A/B when G ignores them
            for n, k in (("A", 0), ("B", 1)):
                if all(g[i] == g[i ^ (1 << k)] for i in range(16)):
                    ports[n] = "1'b0"
            return (f"ALU_RAW_P0 #(.INIT(16'h{init:04x})) {name} (.A({ports['A']}), .B({ports['B']}), "
                    f".COUT({ports['COUT']}));\nassign {ports['SUM']} = {ports['CIN']};  // SUM = 0 ^ CIN")
        for n in unused_inputs(init, fixed, alu=typ == "ALU_RAW"):
            body, k = re.subn(rf"\.{pins[n]}\(\w+\)", f".{pins[n]}(1'b0)", body)
            ties += k
        return f"{typ} {name} ({body});"

    return re.sub(r"^(ALU_RAW|LUT4) (\w+) \((.*?)\);", fix, verilog, flags=re.M | re.S), ties


def expand_defines(verilog: str) -> str:
    """7a. Replace gowin_unpack's `define aliases (used for the clock network) by their targets,
    so drivers and readers use one plain name each."""
    defs = dict(re.findall(r"^`define\s+(\w+)\s+(\w+)\s*$", verilog, re.M))
    for _ in range(8):                             # aliases of aliases
        defs = {k: defs.get(v, v) for k, v in defs.items()}
    body = re.sub(r"^`define\s+\w+\s+\w+\s*\n", "", verilog, flags=re.M)
    return re.sub(r"`(\w+)", lambda m: defs.get(m[1], m[0]), body)


def declare_used(verilog: str) -> tuple[str, int]:
    """7c. Declare every tile wire the netlist uses but never declares (alias targets, joined
    node members): simulators treat implicit nets differently (iverilog rejects them)."""
    used = set(re.findall(r"\bR\d+C\d+_\w+\b", verilog))
    declared = set(re.findall(r"^\s*(?:wire|input|output|inout)\s+(?:\[[^\]]*\]\s*)?(\w+)\s*;", verilog, re.M))
    insts = set(re.findall(r"^\s*[A-Z][A-Z0-9_]*\s+(?:#\(.*?\)\s*)?(\w+)\s*\(", verilog, re.M))
    missing = sorted(used - declared - insts)
    m = re.search(r"^module\s+\w+\s*\(.*?\);\n", verilog, re.M | re.S)
    return verilog[:m.end()] + "".join(f"wire {w};\n" for w in missing) + verilog[m.end():], len(missing)


def resolve_ambiguous_taps(verilog: str, db) -> tuple[str, int, int]:
    """7d. A clock tap (GT00/GT10) can have several spine sources with the SAME fuse set (e.g.
    SPINE24..27 in db tile (19,31) of GW1N-9C): the tile does not encode the choice, and
    gowin_unpack picks one arbitrarily. Per the Apicula maintainer (YosysHQ/apicula#586, 2026-11-09),
    only one of them is physically connected, fixed by the tap column; chipdb records it by putting
    SPINEk into db.nodes only at the tile where it meets the taps. So:
    - SPINE RULE: if exactly one candidate is in db.nodes at this tile, connect that one;
    - otherwise ASSUMPTION A-SPINE (fallback): if exactly one candidate is driven, connect it.
    Returns (text, taps set by the rule, taps set by A-SPINE)."""
    driven = driven_names(verilog)
    node_of = {}
    for name, (typ, members) in db.nodes.items():
        for r, c, w in members:
            node_of[r, c, w] = members
    by_rule = by_assumption = 0

    def fix(m: re.Match) -> str:
        nonlocal by_rule, by_assumption
        dest_name, dest, row, col, src = m[1], m[2], int(m[3]), int(m[4]), m[5]
        r0, c0 = row - 1, col - 1                  # the pip lives in the tap tile (the source's)
        srcs = db[r0, c0].clock_pips.get(dest, {})
        if src not in srcs:
            return m[0]
        cands = [s for s, b in srcs.items() if b == srcs[src]]
        if len(cands) < 2:
            return m[0]
        physical = [s for s in cands if (r0, c0, s) in db.nodes.get(s, ("", set()))[1]]
        if len(physical) == 1:
            by_rule += physical[0] != src
            return m[0] if physical[0] == src else \
                f"assign {dest_name} = R{row}C{col}_{physical[0]};  // SPINE-RULE: tap source by column"
        live = [s for s in cands if any(f"R{r + 1}C{c + 1}_{w}" in driven for r, c, w in node_of.get((r0, c0, s), []))]
        if len(live) != 1 or live[0] == src:
            return m[0]
        by_assumption += 1
        return f"assign {dest_name} = R{row}C{col}_{live[0]};  // A-SPINE: tap source resolved"

    out = re.sub(r"^assign (R\d+C\d+_(GT[01]0)) = R(\d+)C(\d+)_(SPINE\d+);", fix, verilog, flags=re.M)
    return out, by_rule, by_assumption


def clock_nodes(db) -> list[list[tuple[int, int, str]]]:
    """7b. Apicula's own db.nodes for the clock network (GT00/GT10 taps across a column, ...),
    which gowin_unpack only partly expresses through its aliases."""
    return [sorted(members) for name, (typ, members) in db.nodes.items()
            if typ in ("GLOBAL_CLK", "TILE_CLK") and len(members) > 1]


def arg(flag: str) -> str | None:
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else None


_tile_bitmap = chipdb.tile_bitmap


def tile_bitmap_with_empty(db, bitmap, empty=False):
    """5. Decode all-zero tiles too: their default connections (pips with an empty fuse set, e.g.
    E270 <- VCC) are live on the chip and feed neighbouring tiles such as edge IO."""
    return _tile_bitmap(db, bitmap, empty=True)


gowin_unpack.parse_tile_ = parse_tile_all_luts
chipdb.tile_bitmap = tile_bitmap_with_empty
gowin_unpack.tile2verilog = tile2verilog_raw_alus

if __name__ == "__main__":
    sys.argv[0] = "gowin_unpack"
    gowin_unpack.main()
    out, device = arg("-o"), arg("-d")
    stats = {}
    if out and device:
        db = chipdb.load_chipdb(os.path.join(os.path.dirname(apycula.__file__), f"{device}.msgpack.xz"))
        text = expand_defines(open(out).read())
        text, stats["spine_rule_taps_resolved"], stats["a_spine_taps_resolved"] = resolve_ambiguous_taps(text, db)
        text, lw = join_nodes(text, long_wire_nodes(getattr(db, "segments", {})))
        stats.update(lw)
        text, clk = join_nodes(text, clock_nodes(db))
        stats["clock_nodes_joined"], stats["clock_multi_driver_nodes"] = clk["joined_nodes"], clk["multi_driver_nodes"]
        text, stats["input_ties"] = prune_inputs(text)
        text, stats["declared"] = declare_used(text)
        stats["raw_alus"] = len(RAW_ALUS)
        with open(out, "w") as f:
            f.write(text)
        print(f"unpack_full: {stats}", file=sys.stderr)
        with open(out + ".stats.json", "w") as f:       # read by gowinfuzz (assumption labels)
            json.dump(stats, f)

"""carrymut: carry-logic mutants for the iCE40 bitstream-level checker (2026-11-09).

The LUT and routing mutants never touch the carry logic. In an iCE40 logic tile, row 1 holds
the carry-chain bits (icebox: get_carry_cascade_bit = tile[1][49], get_carry_bit = tile[1][50]).
For every fuzz design that uses SB_CARRY, flip the set carry bit of one logic tile that has one,
recover the netlist again and classify the mutant like the others: caught (the proof against the
source fails), equivalent (the mutated netlist is proven equal to the unmutated one), MISSED
(differs from the shipped netlist yet still "proves": a checker hole) or unparseable.
Usage: python3 carrymut.py [workdir]   (default: work/, the combinational flowfuzz corpus)
"""

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pldxfer"))
from pldxfer import fix_vlog, run

from flowfuzz import classify_mutant

CARRY_COLS = (49, 50)


def carry_bits(asc: str) -> list[tuple[int, int]]:
    """(line, column) of every set carry bit (row 1, columns 49/50) of every logic tile."""
    lines = asc.splitlines()
    return [(i + 2, c) for i, line in enumerate(lines) if line.startswith(".logic_tile")
            for c in CARRY_COLS if lines[i + 2][c] == "1"]


def flip(asc: str, line: int, col: int) -> str:
    lines = asc.splitlines()
    lines[line] = lines[line][:col] + ("0" if lines[line][col] == "1" else "1") + lines[line][col + 1:]
    return "\n".join(lines) + "\n"


def mutate(d: Path) -> dict:
    asc = (d / "unpacked.asc").read_text()
    bits = carry_bits(asc)
    if not bits:
        return {"design": d.name, "result": "no_carry_bit"}
    seed = int(d.name[2:])
    line, col = bits[seed % len(bits)]
    (d / "carrymut.asc").write_text(flip(asc, line, col))
    r = run(["icebox_vlog", "-p", str(d / "pins.pcf"), str(d / "carrymut.asc")])
    if r.returncode:
        return {"design": d.name, "result": "unparseable"}
    (d / "carrymut.v").write_text(fix_vlog(r.stdout))
    return {"design": d.name, "bit": [line, col],
            "result": classify_mutant(d, False, "carrymut.v", "carrymut")}


def main(argv: list[str]) -> None:
    work = Path(argv[0]) if argv else Path(__file__).resolve().parent / "work"
    dirs = []
    for d in sorted(work.glob("fz*")):
        j = d / "top.json"
        if j.exists() and (d / "unpacked.asc").exists():   # carry use is read from the bits
            dirs.append(d)
    with ThreadPoolExecutor(8) as ex:
        rows = list(ex.map(mutate, dirs))
    kinds = ("caught", "caught_undriven", "equivalent", "MISSED", "unparseable", "no_carry_bit")
    summary = {"designs_scanned": len(rows),
               "designs_with_a_set_carry_bit": sum(r["result"] != "no_carry_bit" for r in rows),
               **{k: [r["result"] for r in rows].count(k) for k in kinds},
               "missed": [r["design"] for r in rows if r["result"] == "MISSED"]}
    (work / "carrymut.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=1))
    print(json.dumps(summary))


if __name__ == "__main__":
    main(sys.argv[1:])

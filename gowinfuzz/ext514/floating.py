"""Lint: cells whose function depends on an input pin nextpnr left unconnected (relies on the
tile's default input mux). Uses the doc/alu.md ALU model and plain LUT INITs.
Usage: python3 floating.py PNR.json"""
import json
import sys

PINS = ("I0", "I1", "I2", "I3")   # LUT/ALU inputs A, B, C, D


def depends(fn, nvars: int, k: int, fixed: dict[int, int] | None = None) -> bool:
    """Does fn depend on bit k, with the bits in `fixed` held at their constant values?"""
    fixed = fixed or {}
    vs = [v for v in range(1 << nvars) if all((v >> b) & 1 == x for b, x in fixed.items())]
    return any(fn(v) != fn(v ^ (1 << k)) for v in vs)


def consts(conn: dict, names: dict) -> dict[int, int]:
    """Input bit -> constant for pins tied to nextpnr's constant nets."""
    out = {}
    for k, pin in enumerate(PINS):
        n = names.get(tuple(conn.get(pin, [])))
        if n == "$PACKER_VCC":
            out[k] = 1
        elif n == "$PACKER_GND":
            out[k] = 0
    return out


def alu_fns(init: int):
    """SUM and COUT of the raw ALU as functions of (A,B,C,D,CIN) packed into 5 bits."""
    def p(v): return (init >> (v & 15)) & 1
    def g(v): return (init >> (v & 3)) & 1
    def cin(v): return (v >> 4) & 1
    return {"SUM": lambda v: p(v) ^ cin(v), "COUT": lambda v: cin(v) if p(v) else g(v)}


def scan(pnr: dict) -> list[tuple]:
    hits = []
    names = {tuple(x["bits"]): n for n, x in pnr["modules"]["top"]["netnames"].items()}
    for name, c in pnr["modules"]["top"]["cells"].items():
        conn = {k: v for k, v in c["connections"].items() if v}
        if c["type"] == "ALU" and c["parameters"].get("RAW_ALU_LUT"):
            init = int(c["parameters"]["RAW_ALU_LUT"], 2)
            for out, fn in alu_fns(init).items():
                if out not in conn:
                    continue
                for k, pin in enumerate(PINS):
                    if pin not in conn and depends(fn, 5, k, consts(conn, names)):
                        hits.append((name, "ALU", hex(init), out, pin))
        elif c["type"].startswith("LUT") and "INIT" in c["parameters"]:
            n = int(c["type"][3])
            init = int(c["parameters"]["INIT"], 2)
            for k in range(n):
                if f"I{k}" not in conn and depends(lambda v: (init >> v) & 1, n, k, consts(conn, names)):
                    hits.append((name, c["type"], hex(init), "F", f"I{k}"))
    return hits


if __name__ == "__main__":
    h = scan(json.load(open(sys.argv[1])))
    print(len(h), sorted({x[1:] for x in h})[:8])

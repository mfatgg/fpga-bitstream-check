"""memcheck: score a memory-CLB LUT layout on public SLG47910 bitstreams, against controls.

    python3 -I memcheck.py WORK_DIR [--neighbours]

Idea: inside a correct LUT boundary a truth table depends only on the inputs the LUT really uses, and small LUTs
(LUT2-LUT4 gates, muxes) are common. A wrong cell grouping mixes cells of different LUTs, and the mixed "table"
then depends on (nearly) all 6 address bits. So the support-size distribution of the decoded tables separates a
right layout from a wrong one with no vendor report, netlist or vendor tool.

Layouts compared on every memory CLB (columns 6, 8, 22, 24) of every fetched design:
  model      layout.py
  logic      the logic-CLB layout applied to the memory columns (control)
  bandswap   model with the two row-vector sets exchanged between bands (control)
  slotmix    model with the last two groups of slots 0 and 2 exchanged (control: mixes LUTs)
  random     the same 256 cells per CLB in a random partition and order (null, 20 seeds)
Reference: logic CLBs read with the logic-CLB layout.
--neighbours also scores every layout one step away from the model.
"""

import itertools
import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import corpus  # noqa: E402
import layout as L  # noqa: E402

SEEDS = 20
LOW = [sum(1 << a for a in range(64) if not a >> j & 1) for j in range(6)]   # addresses with bit j = 0


def support(table: int) -> int:
    """Number of address bits (0..6) the 64-entry table depends on."""
    return sum(1 for j in range(6) if ((table ^ table >> (1 << j)) & LOW[j]))


def logic_cells_anywhere(x: int, y: int, slot: int) -> list:
    """The logic-CLB layout at any site, memory columns included (a control)."""
    row0 = 32 * y + L.LOGIC_SLOT_ROW[slot]
    block = [(row0 + k, w, x + off) for k in range(16) for (w, off) in L.LOGIC_CELLS]
    out = []
    for i in range(64):
        c = 0
        for j in range(6):
            if i >> j & 1:
                c ^= L.LOGIC_FLIPS[j]
        out.append(block[c])
    return out


BANDSWAP = {0: L.MEM_ROW_VECTORS[64], 16: L.MEM_ROW_VECTORS[64], 64: L.MEM_ROW_VECTORS[0], 80: L.MEM_ROW_VECTORS[0]}
SLOTMIX = dict(L.MEM_GROUPS)
SLOTMIX[0] = L.MEM_GROUPS[0][:2] + L.MEM_GROUPS[2][2:]
SLOTMIX[2] = L.MEM_GROUPS[2][:2] + L.MEM_GROUPS[0][2:]

LAYOUTS = {
    "model": lambda x, y, s: L.memory_lut_cells(x, y, s),
    "logic": logic_cells_anywhere,
    "bandswap": lambda x, y, s: L.memory_lut_cells(x, y, s, vectors=BANDSWAP),
    "slotmix": lambda x, y, s: L.memory_lut_cells(x, y, s, groups=SLOTMIX),
}


def random_layout(rng: random.Random):
    cache = {}

    def f(x, y, s):
        if (x, y) not in cache:
            cells = [c for t in range(4) for c in L.memory_lut_cells(x, y, t)]
            rng.shuffle(cells)
            cache[(x, y)] = cells
        return cache[(x, y)][64 * s:64 * s + 64]
    return f


def distribution(fabrics: list[bytes], lay, columns) -> Counter:
    """Support size -> count over all non-empty slot tables of the CLBs in `columns`."""
    out = Counter()
    for fab in fabrics:
        for y in L.SITE_Y:
            for x in columns:
                for s in range(4):
                    t = L.read_lut(fab, lay(x, y, s))
                    if t:
                        out[support(t)] += 1
    return out


def summary(d: Counter) -> str:
    n = sum(d.values())
    small = sum(v for k, v in d.items() if k <= 4)
    return f"n={n:4d}  support<=4: {small / n:.3f}  full(6): {d[6] / n:.3f}  " + \
        " ".join(f"{k}:{d[k]}" for k in range(7))


def simplicity(fabrics: list[bytes], lay) -> int:
    """Sum of (6 - support) over the non-empty memory-CLB slot tables: higher = more LUT-like."""
    return sum((6 - k) * v for k, v in distribution(fabrics, lay, sorted(L.MEMORY_X)).items())


def neighbours():
    """(name, groups, vectors) for every layout one step from the model: a swap of two groups between slots,
    or a reordering of one band pair's four row vectors."""
    flat = [(s, q) for s in range(4) for q in range(4)]
    for (s1, q1), (s2, q2) in itertools.combinations(flat, 2):
        if s1 == s2:
            continue                       # within-slot swaps only relabel address bits
        g = {s: list(v) for s, v in L.MEM_GROUPS.items()}
        g[s1][q1], g[s2][q2] = g[s2][q2], g[s1][q1]
        yield f"swap s{s1}q{q1}-s{s2}q{q2}", {s: tuple(v) for s, v in g.items()}, L.MEM_ROW_VECTORS
    for bands in ((0, 16), (64, 80)):
        for perm in itertools.permutations(range(4)):
            if perm == (0, 1, 2, 3):
                continue
            vec = dict(L.MEM_ROW_VECTORS)
            for b in bands:
                vec[b] = tuple(L.MEM_ROW_VECTORS[b][i] for i in perm)
            yield f"vectors{bands} perm{perm}", L.MEM_GROUPS, vec


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        print(__doc__)
        return 2
    fabrics = [f for _, f in corpus.load(Path(argv[1]))]
    print(f"{len(fabrics)} distinct public fabrics")
    logic_cols = [x for x in L.SITE_X if x not in L.MEMORY_X]
    mem_cols = sorted(L.MEMORY_X)
    print(f"reference  logic CLBs, logic layout   {summary(distribution(fabrics, logic_cells_anywhere, logic_cols))}")
    for name, lay in LAYOUTS.items():
        print(f"memory CLBs, {name:9s}             {summary(distribution(fabrics, lay, mem_cols))}")
    null = Counter()
    for seed in range(SEEDS):
        null += distribution(fabrics, random_layout(random.Random(seed)), mem_cols)
    print(f"memory CLBs, random x{SEEDS}           {summary(null)}")
    if "--neighbours" not in argv:
        return 0
    base = simplicity(fabrics, LAYOUTS["model"])
    scores = [(simplicity(fabrics, lambda x, y, s, g=g, v=v: L.memory_lut_cells(x, y, s, groups=g, vectors=v)), n)
              for n, g, v in neighbours()]
    better = sum(sc > base for sc, _ in scores)
    ties = sum(sc == base for sc, _ in scores)
    print(f"model simplicity {base}; {len(scores)} neighbours: {better} better, {ties} tied, "
          f"best neighbour {max(scores)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

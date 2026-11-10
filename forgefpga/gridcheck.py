"""gridcheck: derive the CLB site grid and the memory columns from the public bitstreams alone.

    python3 -I gridcheck.py WORK_DIR      (under a minute)

A real LUT site keeps its truth tables small (few inputs used); any other bit position reads routing or other
configuration bits as a "table", which then depends on nearly all 6 inputs. Three scans, each answer-blind:
  rows     every candidate site row y = 0..28 under the logic layout, in the logic columns
  columns  every candidate column x = 1..30 under the logic layout, at the site rows
  kind     per site column: memory-CLB layout vs logic-CLB layout, by memcheck's simplicity score
"""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import corpus  # noqa: E402
import layout as L  # noqa: E402
import memcheck as M  # noqa: E402


def small_share(d: Counter) -> float:
    n = sum(d.values())
    return sum(v for k, v in d.items() if k <= 4) / n if n else 0.0


def scan(fabrics: list[bytes], sites) -> Counter:
    """Support distribution of the logic-layout tables at the given (x, y) sites, all 4 slots."""
    d = Counter()
    for fab in fabrics:
        for x, y in sites:
            for s in range(4):
                t = L.read_lut(fab, M.logic_cells_anywhere(x, y, s))
                if t:
                    d[M.support(t)] += 1
    return d


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    fabrics = [f for _, f in corpus.load(Path(argv[1]))]
    logic_x = [x for x in L.SITE_X if x not in L.MEMORY_X]
    print(f"{len(fabrics)} distinct public fabrics")
    print("rows: y, y mod 3, share of tables with support <= 4")
    for y in range(29):
        print(f"  {y:2d} {y % 3} {small_share(scan(fabrics, [(x, y) for x in logic_x])):.3f}")
    print("columns: x, share of tables with support <= 4")
    for x in range(1, 31):
        print(f"  {x:2d} {small_share(scan(fabrics, [(x, y) for y in L.SITE_Y])):.3f}")
    print("kind: x, simplicity under memory layout, under logic layout, verdict")
    for x in L.SITE_X:
        mem = sum((6 - k) * v for k, v in M.distribution(fabrics, lambda xx, y, s: L.memory_lut_cells(xx, y, s), [x]).items())
        log = sum((6 - k) * v for k, v in M.distribution(fabrics, M.logic_cells_anywhere, [x]).items())
        print(f"  {x:2d} {mem:5d} {log:5d} {'memory' if mem > log else 'logic'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

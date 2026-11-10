"""memsearch: find the memory-CLB LUT layout from public bitstreams alone, from random starts.

    python3 -I memsearch.py WORK_DIR [RESTARTS]      (20 restarts: about 30-50 minutes, under 1 GB)

No vendor report, no netlist, no vendor tool. The only structure assumed is the logic-CLB one: a CLB's LUT cells
are the 16 (word, row band) groups of words 0/1/9/10 x bands +0/+16/+64/+80, 16 rows each, words 0/1 at bit x and
words 9/10 at bit x+1. Unknown: which 4 groups form each slot, and how the 4 low address bits map to the 16 rows of
a group (one GF(2) basis per band). The score is memcheck's simplicity (sum of 6 - support over all non-empty slot
tables): a correct layout keeps each real LUT's table small.
Hill climbing with first improvement: moves swap two groups between slots, replace one basis vector, or swap two
basis vectors. Slot order is irrelevant; of the 24 group orders inside a slot only 3 differ in score (the rest swap
or shift address bits 4/5), and each slot takes the best of those 3. Step 2 then solves the split of the 16 groups
into 4 slots exactly, for the row bases of the best-scoring start. Step 3 (refine_orders) tries every swap of two
row vectors within one band, re-solves step 2 for each, takes the best strict improvement and repeats until none.
"""

import itertools
import random
import sys
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import corpus  # noqa: E402
import memcheck as M  # noqa: E402
import layout as L  # noqa: E402

GROUPS = tuple((w, band, 0 if w in (0, 1) else 1) for w in (0, 1, 9, 10) for band in (0, 16, 64, 80))
BANDS = (0, 16, 64, 80)


def group_columns(raws: list[bytes]) -> list[tuple[int, ...]]:
    """Per non-empty memory CLB of every design: the 16 groups as 16-bit row values."""
    out = []
    for raw in raws:
        for y in L.SITE_Y:
            for x in sorted(L.MEMORY_X):
                vals = tuple(sum(L.bit(raw, 32 * y + band + r, w, x + bo) << r for r in range(16))
                             for w, band, bo in GROUPS)
                if any(vals):
                    out.append(vals)
    return out


def rank4(vecs) -> bool:
    span = {0}
    for v in vecs:
        span |= {s ^ v for s in span}
    return len(span) == 16


def row_perm(basis) -> tuple[int, ...]:
    out = []
    for a in range(16):
        r = 0
        for k in range(4):
            if a >> k & 1:
                r ^= basis[k]
        out.append(r)
    return tuple(out)


class Scorer:
    def __init__(self, clbs):
        self.clbs = clbs

        @lru_cache(maxsize=None)
        def ordered_score(groups: tuple, bases: tuple) -> int:   # groups in address order q = a4 + 2*a5
            perms = [row_perm(b) for b in bases]
            total = 0
            for vals in self.clbs:
                t = 0
                for q, (g, perm) in enumerate(zip(groups, perms)):
                    v = vals[g]
                    if v:
                        t |= sum((v >> perm[a] & 1) << a for a in range(16)) << 16 * q
                if t:
                    total += 6 - M.support(t)
            return total

        def slot_score(groups: tuple, bases: tuple) -> int:
            """Best of the 3 group orders that differ beyond swapping/shifting a4, a5 (S4 / that group of 8)."""
            g, b = groups, bases
            return max(ordered_score(tuple(g[i] for i in o), tuple(b[i] for i in o))
                       for o in ((0, 1, 2, 3), (0, 2, 3, 1), (0, 1, 3, 2)))
        self.slot_score = slot_score

    def score(self, slots, bases) -> int:
        return sum(self.slot_score(tuple(sorted(s)), tuple(bases[GROUPS[g][1]] for g in sorted(s))) for s in slots)


def random_basis(rng):
    while True:
        b = tuple(rng.randrange(1, 16) for _ in range(4))
        if rank4(b):
            return b


def climb(scorer: Scorer, rng: random.Random, log=print):
    groups = list(range(16))
    rng.shuffle(groups)
    slots = [groups[4 * i:4 * i + 4] for i in range(4)]
    bases = {b: random_basis(rng) for b in BANDS}
    best = scorer.score(slots, bases)
    improved = True
    while improved:
        improved = False
        moves = [("swap", i, a, j, b) for i in range(4) for j in range(i + 1, 4) for a in range(4) for b in range(4)]
        moves += [("vec", band, k, v) for band in BANDS for k in range(4) for v in range(1, 16)]
        moves += [("order", band, k, l) for band in BANDS for k in range(4) for l in range(k + 1, 4)]
        rng.shuffle(moves)
        for m in moves:
            if m[0] == "swap":
                _, i, a, j, b = m
                new_slots = [list(s) for s in slots]
                new_slots[i][a], new_slots[j][b] = new_slots[j][b], new_slots[i][a]
                new_bases = bases
            elif m[0] == "order":
                _, band, k, l = m
                nb = list(bases[band])
                nb[k], nb[l] = nb[l], nb[k]
                new_slots, new_bases = slots, {**bases, band: tuple(nb)}
            else:
                _, band, k, v = m
                nb = list(bases[band])
                if nb[k] == v:
                    continue
                nb[k] = v
                if not rank4(nb):
                    continue
                new_slots, new_bases = slots, {**bases, band: tuple(nb)}
            sc = scorer.score(new_slots, new_bases)
            if sc > best:
                slots, bases, best, improved = new_slots, new_bases, sc, True
                break
    return best, slots, bases


def exact_partition(scorer: Scorer, bases) -> tuple[int, list]:
    """Best split of the 16 groups into 4 slots for FIXED bases: score every 4-subset, then search partitions."""
    subset_score = {}
    for a in range(16):
        for b in range(a + 1, 16):
            for c in range(b + 1, 16):
                for d in range(c + 1, 16):
                    g = (a, b, c, d)
                    subset_score[g] = scorer.slot_score(g, tuple(bases[GROUPS[i][1]] for i in g))
    best = (-1, [])

    def rec(left: tuple, chosen: list, total: int):
        nonlocal best
        if not left:
            if total > best[0]:
                best = (total, list(chosen))
            return
        first, rest = left[0], left[1:]
        for i in range(len(rest)):
            for j in range(i + 1, len(rest)):
                for k in range(j + 1, len(rest)):
                    g = (first, rest[i], rest[j], rest[k])
                    rec(tuple(x for x in rest if x not in g), chosen + [g], total + subset_score[g])
    rec(tuple(range(16)), [], 0)
    return best


def refine_orders(scorer: Scorer, bases, score: int, part, log=print):
    """Coordinate ascent over vector transpositions per band, each scored by the exact partition (answer-blind)."""
    while True:
        cands = []
        for band in BANDS:
            for k, l in itertools.combinations(range(4), 2):
                nb = list(bases[band])
                nb[k], nb[l] = nb[l], nb[k]
                nbases = {**bases, band: tuple(nb)}
                cands.append((exact_partition(scorer, nbases), band, (k, l), nbases))
        (s2, p2), band, kl, nbases = max(cands, key=lambda c: c[0][0])
        log(f"  step 3: best swap band +{band} vectors {kl}: {s2} ({'accepted' if s2 > score else 'stop'})", flush=True)
        if s2 <= score:
            return score, part, bases
        score, part, bases = s2, p2, nbases


def model_layout():
    idx = {g: i for i, g in enumerate(GROUPS)}
    slots = [[idx[g] for g in L.MEM_GROUPS[s]] for s in range(4)]
    return slots, dict(L.MEM_ROW_VECTORS)


def same_partition(a, b) -> bool:
    return {frozenset(s) for s in a} == {frozenset(s) for s in b}


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        print(__doc__)
        return 2
    restarts = int(argv[2]) if len(argv) == 3 else 8
    raws = [f for _, f in corpus.load(Path(argv[1]))]
    clbs = group_columns(raws)
    scorer = Scorer(clbs)
    ms, mb = model_layout()
    model_score = scorer.score(ms, mb)
    print(f"{len(raws)} distinct public fabrics, {len(clbs)} non-empty memory CLBs; model score {model_score}", flush=True)
    runs = []
    for seed in range(restarts):
        best, slots, bases = climb(scorer, random.Random(seed))
        runs.append((best, seed, bases))
        spans_equal = all(set(row_perm(bases[b])) == set(range(16)) for b in BANDS)
        print(f"seed {seed}: score {best} ({'=' if best == model_score else '>' if best > model_score else '<'} model) "
              f"partition {'== model' if same_partition(slots, ms) else 'differs'}; "
              f"bases {[bases[b] for b in BANDS]} rank-ok {spans_equal}", flush=True)
    top, seed, bases = max(runs)
    score, part = exact_partition(scorer, bases)
    print(f"stage 2 (bases of the best-scoring start, seed {seed}, score {top}): exact partition score {score} "
          f"({'=' if score == model_score else '>' if score > model_score else '<'} model {model_score}); "
          f"partition {'== model' if same_partition(part, ms) else 'differs'}", flush=True)
    score, part, bases = refine_orders(scorer, bases, score, part)
    print(f"stage 3: score {score} ({'=' if score == model_score else '>' if score > model_score else '<'} model "
          f"{model_score}); partition {'== model' if same_partition(part, ms) else 'differs'}; "
          f"bases {[bases[b] for b in BANDS]}", flush=True)
    print("  slots:", [[GROUPS[g] for g in sl] for sl in part])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

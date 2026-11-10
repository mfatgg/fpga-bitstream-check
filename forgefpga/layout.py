"""layout: where a LUT's 64 truth-table cells sit in the Renesas ForgeFPGA SLG47910 configuration fabric.

The fabric is 1,024 rows x 11 little-endian 32-bit words (45,056 bytes; at byte 1,320 of an MCU bitstream).
A cell is (row, word, bit). CLB sites (x, y): x in {2, 4, ..., 28}, y in {1, 4, ..., 28}, 140 CLBs; a site uses
rows 32*y .. 32*y + 95 and bits x and x + 1 of a word. Columns x in {6, 8, 22, 24} are memory CLBs (40), which
store LUTs differently from the 100 logic CLBs.

LOGIC CLB, slot s: a 16-row block at rows 32*y + {0, 16, 64, 80}[s] with cells (w0, x), (w1, x), (w9, x+1),
(w10, x+1); block index c = row_in_block << 2 | cell. Truth-table entry i sits at c = offset ^ XOR of FLIPS[pins[j]]
over the set bits j of i, FLIPS = (12, 16, 4, 32, 1, 3), one per physical LUT input.

MEMORY CLB, slot s: 4 groups of 16 cells, chosen by physical inputs 4/5 (q = a4 + 2*a5). Inside a group the row
is an XOR of one vector per physical input 0..3; the vectors depend on the row band: bands +0/+16 -> (8, 4, 3, 1),
bands +64/+80 -> (8, 4, 1, 2). The groups (word, band, bit offset) are MEM_GROUPS below; together they partition
words 0/1/9/10 x bands 0/16/64/80. Recovered from public bitstreams alone: memsearch.py, README.md.

In both layouts the place-and-route tool picks, per LUT, which physical input carries each truth-table input
(`pins`) and the cell of the all-zero input (`offset`); a reader searches them, a writer takes them from its routing.
"""

ROWS, WORDS = 1024, 11
SITE_X = tuple(range(2, 29, 2))
SITE_Y = tuple(range(1, 29, 3))
MEMORY_X = frozenset({6, 8, 22, 24})
LOGIC_SLOT_ROW = {0: 0, 1: 16, 2: 64, 3: 80}
LOGIC_CELLS = ((0, 0), (1, 0), (9, 1), (10, 1))                 # (word, bit offset from x)
LOGIC_FLIPS = (12, 16, 4, 32, 1, 3)                             # per physical input
MEM_GROUPS = {                                                  # slot -> 4 x (word, row band, bit offset), q order
    0: ((1, 0, 0), (0, 0, 0), (9, 80, 1), (9, 64, 1)),
    1: ((1, 16, 0), (0, 16, 0), (10, 80, 1), (10, 64, 1)),
    2: ((10, 0, 1), (9, 0, 1), (0, 64, 0), (0, 80, 0)),
    3: ((10, 16, 1), (9, 16, 1), (1, 64, 0), (1, 80, 0)),
}
MEM_ROW_VECTORS = {0: (8, 4, 3, 1), 16: (8, 4, 3, 1), 64: (8, 4, 1, 2), 80: (8, 4, 1, 2)}
IDENTITY_PINS = (0, 1, 2, 3, 4, 5)


def _check(x: int, y: int, slot: int, pins: tuple, offset: int) -> None:
    if x not in SITE_X or y not in SITE_Y:
        raise ValueError(f"({x}, {y}) is not a CLB site")
    if slot not in range(4):
        raise ValueError(f"slot must be 0..3, got {slot}")
    if sorted(pins) != list(range(6)) or not 0 <= offset < 64:
        raise ValueError("pins must be a permutation of 0..5 and offset in 0..63")


def logic_lut_cells(x: int, y: int, slot: int, pins: tuple = IDENTITY_PINS, offset: int = 0) -> list:
    """64 (row, word, bit) cells of a logic-CLB LUT; cell i holds truth-table entry i."""
    _check(x, y, slot, pins, offset)
    if x in MEMORY_X:
        raise ValueError(f"({x}, {y}) is a memory CLB")
    row0 = 32 * y + LOGIC_SLOT_ROW[slot]
    block = [(row0 + k, w, x + off) for k in range(16) for (w, off) in LOGIC_CELLS]
    out = []
    for i in range(64):
        c = offset
        for j in range(6):
            if i >> j & 1:
                c ^= LOGIC_FLIPS[pins[j]]
        out.append(block[c])
    return out


def memory_lut_cells(x: int, y: int, slot: int, pins: tuple = IDENTITY_PINS, offset: int = 0,
                     groups: dict = MEM_GROUPS, vectors: dict = MEM_ROW_VECTORS) -> list:
    """64 (row, word, bit) cells of a memory-CLB LUT; cell i holds truth-table entry i.
    `groups` / `vectors` exist so the checks can score alternative layouts."""
    _check(x, y, slot, pins, offset)
    out = []
    for i in range(64):
        a = offset
        for j in range(6):
            if i >> j & 1:
                a ^= 1 << pins[j]
        w, band, bo = groups[slot][a >> 4]
        r = 0
        for k in range(4):
            if a >> k & 1:
                r ^= vectors[band][k]
        out.append((32 * y + band + r, w, x + bo))
    return out


def lut_cells(x: int, y: int, slot: int, pins: tuple = IDENTITY_PINS, offset: int = 0) -> list:
    if x in MEMORY_X:
        return memory_lut_cells(x, y, slot, pins, offset)
    return logic_lut_cells(x, y, slot, pins, offset)


def bit(fabric: bytes, row: int, word: int, b: int) -> int:
    i = (row * WORDS + word) * 32 + b
    return fabric[i >> 3] >> (i & 7) & 1


def read_lut(fabric: bytes, cells: list) -> int:
    """The 64-entry truth table stored in `cells` (bit i = entry i)."""
    return sum(bit(fabric, *c) << i for i, c in enumerate(cells))

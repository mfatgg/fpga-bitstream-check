# forgefpga: the SLG47910 LUT layout, memory CLBs included, from public bitstreams alone

The Renesas ForgeFPGA SLG47910 (1K LUTs, as on the Vicharak Shrike-lite) has no published bitstream format. This folder documents **where every LUT's truth table sits in the configuration fabric**, for both kinds of CLB, and the check that recovers the harder half (the 40 memory CLBs) **from public, openly licensed bitstreams only**. No vendor report, no netlist, no vendor tool, and no build of my own goes into it.

| File | What |
|---|---|
| [`layout.py`](layout.py) | The layout: `lut_cells(x, y, slot, pins, offset)` gives the 64 (row, word, bit) cells of a LUT; `read_lut` reads a truth table |
| [`memsearch.py`](memsearch.py) | Finds the memory-CLB layout from random starts by a simplicity score, then solves the slot split exactly |
| [`memcheck.py`](memcheck.py) | Scores a layout against controls, a random null and all 142 one-step neighbours |
| [`gridcheck.py`](gridcheck.py) | Derives the site grid and which columns are memory CLBs, from the same public files |
| [`corpus.py`](corpus.py) | Reads the fabric of every bitstream container under the fetched sources, each distinct fabric once |
| [`sources.tsv`](sources.tsv), [`fetch.sh`](fetch.sh) | The 9 third-party repos used, pinned to commits, with their licences. Nothing of theirs is redistributed here |
| [`tests/`](tests/test_memlayout.py) | Layout invariants, loader, and recovery of a planted layout from synthetic LUTs |

## The layout
The fabric is 1,024 rows × 11 little-endian 32-bit words (45,056 bytes; at byte 1,320 of an MCU-mode bitstream). CLB sites are x ∈ {2, 4, …, 28}, y ∈ {1, 4, …, 28} (140 CLBs). A site uses rows 32·y … 32·y + 95 and bits x and x + 1. Each CLB has 4 LUT6 slots.

**Logic CLBs (100):** slot s is a 16-row block at rows 32·y + {0, 16, 64, 80}[s] with 4 cells per row: (word 0, bit x), (w1, x), (w9, x+1), (w10, x+1). Truth-table entry i sits at block index `offset ⊕ XOR of FLIPS[pins[j]]` over the set bits j of i, with FLIPS = (12, 16, 4, 32, 1, 3).

**Memory CLBs (40, columns x = 6, 8, 22, 24):** the same 256 cells are used, but grouped differently. Each slot takes 4 groups of 16 cells, one group per value of physical inputs 4/5. Inside a group, the row is the XOR of one vector per physical input 0..3. Bands +0/+16 use (8, 4, 3, 1) and bands +64/+80 use (8, 4, 1, 2).

| Slot | q = 0 | q = 1 | q = 2 | q = 3 |
|---|---|---|---|---|
| 0 | w1, +0, x | w0, +0, x | w9, +80, x+1 | w9, +64, x+1 |
| 1 | w1, +16, x | w0, +16, x | w10, +80, x+1 | w10, +64, x+1 |
| 2 | w10, +0, x+1 | w9, +0, x+1 | w0, +64, x | w0, +80, x |
| 3 | w10, +16, x+1 | w9, +16, x+1 | w1, +64, x | w1, +80, x |

In both kinds, the place-and-route tool chooses per LUT which physical input carries each truth-table input (`pins`) and where the all-zero input sits (`offset`, which absorbs inverting routes). A reader searches over them; a writer takes them from its own routing. **Which physical input is which routing wire is still open.**

## How the memory layout is recovered
- **Inputs:** 79 distinct fabrics from the 9 repos in `sources.tsv`: Vicharak's examples plus 8 community repos (GPL-2.0/3.0, CERN-OHL-1.2, BSD-3, MIT). The fabrics include 588 non-empty memory CLBs.
- **Assumed structure:** only the logic-CLB one: words 0/1/9/10 and bands +0/+16/+64/+80. That gives 16 candidate cell groups of 16 rows per CLB. The logic layout comes from symmetry statistics of LUT tables in these fabrics and from the one openly licensed vendor build report among them (Arnav-panjla, GPL-3.0): each of its 6 LUT6 tables is found exactly once in the whole fabric, at the predicted block.
- **Site grid and memory columns, derived (`gridcheck.py`):** read as logic LUTs, the tables are small only at real sites.
  - Rows y ≡ 1 (mod 3), y = 1…28: 40–54 % of tables have support ≤ 4. Every other row: 0–10 %.
  - Even columns x = 2…28: 29–54 %. Every odd column and x = 30: 0.0–0.3 %.
  - Columns 6, 8, 22 and 24, and only those, score higher under the memory layout than under the logic layout. Simplicity there is 1,173 vs 718, 1,203 vs 758, 965 vs 578 and 850 vs 524; elsewhere the logic layout wins by about 2:1.
- **Unknown:** which 4 groups form each slot, and how the 4 low address bits map to the 16 rows (one GF(2) basis per band).
- **Statistic:** with the right grouping, each truth table depends only on the inputs its LUT really uses, and small LUTs are common. A wrong grouping mixes LUTs, and the mixed table depends on nearly all 6 inputs. Score = Σ (6 − support) over all non-empty slot tables.
- **Search:**
  1. 20 hill-climbing starts from random layouts. Moves swap groups between slots and replace or swap basis vectors.
  2. For the best-scoring start's bases, the exact best split of the 16 groups into 4 slots: all 1,820 four-group subsets, all partitions.
  3. Swap two row vectors within one band, re-solve step 2 for each such swap, keep the best strict improvement, and repeat until nothing improves.

## Results (2026-11-10, on a fresh `fetch.sh`)
**The search ends on the layout above.** It finds the same 4 slot group sets and a score of 4,191, equal to the layout's own. Its row bases, (4, 1, 8, 3) for bands +0/+16 and (4, 2, 8, 1) for +64/+80, are the layout's under one consistent relabelling of address bits 0–3. The router chooses that relabelling freely, so it isn't a difference.
- Steps 1–2: all 20 starts find the right four vectors per band, but not their order. The best start reaches 3,677, and step 2 gives 3,879 with a different split.
- Step 3: one swap (band +0, vectors 0 and 2) gives 4,191 with the layout's split. The best next swap scores 3,907, so the search stops.
- Full log: `python3 -I memsearch.py work 20`, 22.5 minutes, under 90 MB of memory.

| Layout on the memory CLBs | Tables | Support ≤ 4 | Full support (6) |
|---|---|---|---|
| **this layout** | 2,117 | **48.7 %** | 31.3 % |
| logic-CLB layout (control) | 2,221 | 34.6 % | 50.2 % |
| row-vector sets swapped between bands (control) | 2,117 | 39.7 % | 44.6 % |
| two groups swapped between slots 0 and 2 (control) | 2,179 | 47.1 % | 36.7 % |
| random partition of the same cells, 20 seeds (null) | 46,670 | 0.0 % | 99.9 % |
| *reference: logic CLBs under the logic layout* | *5,833* | *45.9 %* | *33.4 %* |

- **Neighbours:** all 142 layouts one step away score lower: 0 better, 0 tied, best 3,566 against 4,191.
- **Split-half:** for a random split of the designs into 39 and 40, each half on its own gives the same exact partition, which ties the layout's score on that half.

## Limits
- **Not a blind clean room.** The layout was first found in earlier, unpublished work. I wrote this search afterwards and knew the answer while doing so. I revised it three times after it failed:
  - only 3 of the 24 group orders inside a slot change the score, so the search takes the best of those 3;
  - plain hill climbing stalls, hence step 2;
  - on the strictly public file set, steps 1–2 alone end on the wrong vector order, hence step 3.

  Each step only takes the highest score; none of them compares against the answer. An earlier local run had also, by mistake, included two fabrics of my own vendor builds. They are excluded here: `fetch.sh` gets only the files listed in `sources.tsv`.
- The split-half check uses the row bases found on all designs.
- Pin order inside a LUT is fixed only up to the relabelling the router chooses anyway.
- **Not yet confirmed on silicon.** A Shrike-lite test that inverts single LUTs in a GPL design, memory slots included, is prepared and waits for boards.
- The other configuration bits (routing, flip-flop and IO configuration) aren't covered here.

## Run
```bash
./fetch.sh work
```
```bash
python3 -I -m unittest discover -s tests
```
```bash
python3 -I gridcheck.py work
```
```bash
python3 -I memcheck.py work --neighbours
```
```bash
python3 -I memsearch.py work 20
```
gridcheck takes under a minute and memcheck about 2.5 minutes; memsearch with 20 starts takes about 25 minutes. Both use the standard library only (Python 3.11+).

**Sources and licences:** the bitstreams are their authors' and are fetched, not copied: see `sources.tsv`. Facts about the bit layout are stated in my own words. Renesas' public datasheet and configuration guide were read for the fabric size and framing; nothing from them is reproduced.

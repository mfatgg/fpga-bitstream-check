# flowfuzz: fuzzing the open iCE40 flow, checked on the shipped bitstream

**Why:** FUZNET (Cosgrove/Donaldson/Wickerson, FPGA '26) found 2 real Vivado place-and-route bugs in 57,316 random netlists. The authors "ended up only managing to test Vivado": nextpnr's implicit I/O connections made it hard to extract the netlists before and after place-and-route. flowfuzz avoids that by checking the **final `.bin`**:

```
random 8-bit arithmetic/logic design (+ − * ^ & | ~ shifts, comparisons, ?:, constants)
  → yosys synth_ice40 → nextpnr-ice40 (UP5K, seed varies) → icepack
  → iceunpack → icebox_vlog → yosys miter + SAT against the source (complete proof, combinational)
  + negative control on every design: the same design with one output bit flipped must FAIL
  + bitstream mutant on every design: one used LUT inverted in the unpacked image must FAIL;
    if it doesn't, the mutated netlist is proven against the unmutated one ("equivalent" =
    dead/masked logic; anything else would be a checker hole, "MISSED")
  + checker self-test before each run (a known non-equivalent pair must give a counterexample)
  + designs that synthesize to 0 LUTs (bare wires/constants) are skipped and counted
```

It reuses `build_src`/`prove` from [pldxfer](../pldxfer/README.md).

## Results (2026-11-07; yosys 0.70+post, nextpnr-ice40 from Linuxbrew, icestorm 1.1)
| Run | Designs | Proved equivalent | Tool bugs found | Negative controls caught | Bitstream mutants | Time |
|---|---|---|---|---|---|---|
| 2026-11-07, strict checker | 400, but **142 trivial** (0 LUTs) | 400 | 0 | 400 / 400 | – | about 1.8 s per design |
| **2026-11-08, non-trivial corpus** | **400** (82 trivial ones skipped); LUTs min/median/max **1 / 14 / 137** | **400** | **0** | **400 / 400** | **375 caught, 25 equivalent, 0 missed** | about 2.8 s per design |

**Correction (2026-11-08):** the first run's corpus was 35 % trivial. The generator could stop at the top level and emit a bare wire or constant (`r = b`), which tests only routing. The second run fixes that and is the one to cite. Logs: `run400-v1-trivial-included.log` (old), `run400.log` (new), `run400-mutants-classified.json` (the 25 equivalent mutants; each was proven equivalent to the unmutated netlist, for example a LUT whose output is not routed anywhere).

**The real finding was in my own checker, not in the tools.**
- The first run used `miter -ignore_gold_x`. Without undef modelling, that ignores every reference bit that is 0.
- A negative control exposed it: a constant-0 design "matched" a bitstream computing `~a`.
- The fix is now a self-test, and pldxfer was re-run under the strict check (still proves).
- Lesson: every equivalence claim needs negative controls, or it may be vacuous.

## Sequential designs and routing mutants (`seqfuzz.py`, 2026-11-08)
- **Designs:** a 4-bit state register `q` (power-up 0), `q <= f(a, b, q)`, outputs `{r[7:4], q}` with `r = g(a, b, q)`. Random expressions come from the same generator, with `q` as an extra leaf.
- **Proof:** a bounded check to depth 20 from power-up. It is **complete** when the gate side has as many flip-flop *bits* as the source (`pldxfer.complete_proof`; 20 > 2⁴). It is labelled bounded-only when synthesis removed state bits.
- **Controls per design:** an output-bit flip, one inverted LUT, and one cleared routing bit (in a logic tile that holds a used LUT) in the unpacked image.

| Run | Designs | Proved | Complete proofs | Output controls caught | LUT mutants | Routing mutants |
|---|---|---|---|---|---|---|
| 2026-11-08 | 100 | **100** | 68 (32 bounded-only: synthesis removed state bits) | **100 / 100** | 93 caught, 7 equivalent, 0 missed | **80 caught, 18 equivalent, 0 missed** (2 designs had no routing bit in a LUT tile) |

**Two more checker bugs found and fixed on the way:**
1. **`pldxfer.count_ffs` counted flip-flop *cells*, not bits.** A 4-bit register is one `$dff` cell, so the count gave 1 vs 4.
   - **Fix:** techmap to single-bit cells first.
   - pldxfer's own "4 vs 4, complete" claim still holds when re-run, because its registers were single-bit.
2. **`prove()` ignored extra ports.** A routing mutant (seed 85) connected an extra input pad (`io_16_0_1`). `miter` matches ports by name and left it floating, so the mutant looked equivalent to the source but not to the shipped netlist ("MISSED").
   - **Fix:** `prove()` now fails on any port-list difference.
   - A re-scan of all 400 combinational and 100 sequential main proofs found no port mismatch, so earlier results stand. Seed 85 is now "caught" (`seq100-summary-corrected.json`).

## Carry-logic mutants (`carrymut.py`, 2026-11-09)
- **Method:** flip one set carry bit (logic-tile row 1, columns 49/50, the carry enable and cascade bits) in the unpacked image of every fuzz design that has one. Classified like the other mutants.
- **Result (combinational corpus):** 177 designs have a set carry bit.
  - 147 caught; 9 caught as structural changes (the mutant leaves undriven fan-in, which `prove()` refuses to prove); 21 proven equivalent; **0 missed**.
  - Together with the LUT and routing mutants, every kind of logic-tile bit the checker relies on now has a negative control.
- `classify_mutant` now reports the undriven case as `caught_undriven`. Before this it would have shown up as MISSED, because `prove()` returns no counterexample there.

**Limits:**
- 8 inputs and 8 outputs; sequential designs have one 4-bit register and one clock.
- No BRAM, DSP, carry-heavy wide arithmetic, or sequential or multi-clock logic. FUZNET's Vivado bugs were in CFGLUT5 retargeting and CARRY4 constant propagation, a design space richer than this one.
- Zero bugs in 400 designs says little about the tools.
- The combinational run's bitstream mutant only inverts whole LUTs. Routing bits are covered by the sequential run (one cleared bit per design), and carry bits by `carrymut.py`.

```bash
python3 flowfuzz/flowfuzz.py 400
```
```bash
python3 -m unittest discover -s flowfuzz/tests
```

# gowinfuzz: bitstream-level fuzzing of the open Gowin flow (2026-11-08)

**Status 2026-11-09:** see *Final results* below.

**Question:** does the [flowfuzz](../flowfuzz/README.md) method carry over to Gowin? It checks random designs against the netlist recovered from the *final bitstream*. On Gowin that flow is yosys `synth_gowin` → nextpnr-himbaechel → Apicula `gowin_pack` → `gowin_unpack`, on a GW1N-9C (Tang Nano 9K). Apicula is much younger than icestorm, so finding real problems is more likely.

**Setup:**
- (2026-11-09: `unpack_full.py` now carries four decode fixes; see the results section.)
- Local venv `.venv`: Apycula 0.32 and yowasp-nextpnr-himbaechel-gowin 0.11.1.0.post826 (WebAssembly build from PyPI); yosys 0.70.
- `unpack_full.py` wraps `gowin_unpack` with one fix: it emits constant-1 LUTs (see [contrib/apicula-unpack-repro](../../contrib/apicula-unpack-repro/README.md), issue 1).
- `alu_apicula.v`: yosys's ALU model with the extra unused `I2` port that `gowin_unpack` writes.

**Per design:**
- The output netlist from `gowin_unpack` is wrapped back onto `i0..i7/o0..o7` (pad map from nextpnr's IO BELs; unused pads tied to 0).
- Two oracles:
  - a SAT miter, with each side elaborated separately;
  - exhaustive iverilog simulation of all 256 inputs.
- An output-bit negative control.
- Failures are classified, and are never counted as tool bugs until localised:
  - `unpack_limit`: the design has `RAW_ALU_LUT` ALUs, which `gowin_unpack` decodes as C2L (issue 2);
  - `unpack_gap`: there are undriven wires in the output fan-in (long-wire taps, note 3);
  - `counterexample`: everything else.

## Results (2026-11-08, strict rule: undriven fan-in = `unpack_gap`, even where SAT said "proved")
| Corpus | Designs (trivial skipped) | Cells min/median/max | **Proved** (both oracles agree) | `unpack_limit` (raw ALU) | `unpack_gap` (undriven fan-in) | Counterexamples | Oracle disagreements | Flow errors |
|---|---|---|---|---|---|---|---|---|
| `-noalu` (LUT/MUX only) | 60 (10) | 1 / 32 / 128 | **20**, 20/20 negative controls caught | 0 | 39 | **0** | 0 | 1 (synthesis timeout) |
| default (ALU carry chains) | 60 (10) | 1 / 18 / 420 | **7** | 50 | 53 (overlaps the 50) | **0** | 0 | 0 |

- **Read:**
  - Every design whose recovered netlist is complete proves.
  - Every failure traces to a gap in `gowin_unpack`: long wires in all 39 `-noalu` cases, and raw ALUs. **No nextpnr, yosys or `gowin_pack` bug was shown.**
  - Coverage is the real result: only 27 of 120 designs could be checked at all, so a bitstream-level checker for Gowin needs a more complete unpacker first (long wires, raw ALUs).
- **Why the strict rule:** in the ALU run, seed 2 was "proved" by SAT while the exhaustive simulation gave X. With its 3 undriven ALU inputs tied to 0 the proof holds; tied to 1 it fails. The miter treats undriven nets as don't-care, so such a "proof" is unsound.
  - The same check now guards the iCE40 prover (`pldxfer.prove`). An audit of all 582 iCE40 netlists from the published runs found **no** undriven fan-in, so the iCE40 results stand.
  - The check itself has a negative control: deleting `o0`'s driver is flagged.
- Logs:
  - `run60-noalu.log` and `run60-alu.log` are the raw runs;
  - `*.reclassified.json` files apply the strict rule;
  - `*-v1-falsecex.log` are the first runs, with the broken proof flow.

## Final results (2026-11-09, `runs-v5/`, one code version for every number)
`unpack_full.py` now carries seven decode fixes for `gowin_unpack`:
1. constant-1 LUTs;
2. long-wire nodes;
3. raw/C2L/ONE2C/mode-5 ALUs as `ALU_RAW`;
4. provably ignored inputs;
5. all-zero tiles' default pips;
6. alone-node fuses;
7. the clock network (`` `define `` expansion, `db.nodes` clock nodes, declarations), with ambiguous clock taps resolved by the tap-column rule (apicula#586; assumption A-SPINE only as a fallback, never needed so far).

| Corpus | **Proved** (SAT and simulation agree) | Not proved | Notes |
|---|---|---|---|
| GW1N-9C, `-noalu` | **59 / 60** | 1 synthesis timeout (seed 33: ABC9 with `-noalu`) | – |
| GW1N-9C, ALU | **59 / 60** | 1 **real nextpnr bug** (seed 21) | – |
| GW1N-9C, sequential | **40 / 40** | – | 31 complete (4/4 state bits kept), 37 under A-SPINE |
| GW2A-18C, ALU | **39 / 40** | the same nextpnr bug (seed 21) | – |
| GW2A-18C, sequential | **20 / 20** | – | 18 complete, 18 under A-SPINE |

**Read:**
- **217 of 220 designs proved**, with 0 counterexamples and 0 disagreements between SAT and simulation (exhaustive for combinational designs, 300 random cycles for sequential ones). All 217 negative controls were caught.
- **The real nextpnr bug (seed 21):** the old carry-from-logic chain head `RAW_ALU_LUT 0x505a` (`COUT = I0 | CIN`, with `CIN` unconnected). It is fixed upstream in [6030081a15](https://github.com/YosysHQ/nextpnr/commit/6030081a15) (2026-09-13) but still present in the newest yowasp build on PyPI (0.11.1.0.post826, 2026-08-13). The checker flagged it on both families.
- **Clock taps (updated 2026-11-09):** the Apicula maintainer answered #586: only one of the spines with an identical fuse set is physically connected, fixed by the tap column; chipdb records it in `db.nodes`. `unpack_full.py` now uses that rule: it resolves 180/188 ambiguous groups on GW1N-9C and 216/224 on GW2A-18C uniquely. All 87 earlier A-SPINE choices (59 GW1N, 28 GW2A), and all taps in the #514 builds, agree with it. The rule gives no answer only in column 27 and the last column of the two spine rows; there A-SPINE remains a labelled fallback. "Under A-SPINE" in the table above therefore now reads "under the column rule".
- **Not verified:**
  - anything on real hardware;
  - the `ALU_RAW` semantics, which come from Apicula's `doc/alu.md`, agree with yosys's model for all 10 modes, and match the nextpnr commit's own `COUT = I0 | CIN` analysis.
- **Earlier runs** (`runs-v2` … `runs-v4`, `run60-*`) document the path there; cite `runs-v5` only.

## Results with the completed unpacker (2026-11-09, `runs-v2/`)
`unpack_full.py` now fixes four `gowin_unpack` gaps:
1. it emits constant-1 LUTs;
2. it joins long-wire nodes, using nextpnr's `db.segments` rule;
3. it decodes every ALU without a faithful named mode (raw LUT, C2L, ONE2C, mode 5) from its LUT bits as `ALU_RAW`;
4. it ties cell inputs the function provably ignores, given VCC/VSS-tied inputs.

| Corpus | **Proved** (SAT and exhaustive simulation agree) | Gaps left | Counterexamples | Oracle disagreements | Before (strict rule, 2026-11-08) |
|---|---|---|---|---|---|
| `-noalu` | **59 / 60** (59/59 negative controls) | 0 (1 synthesis timeout) | 0 | 0 | 20 |
| default (ALU) | **57 / 60** (57/57 negative controls) | 3: one chain-head `CIN`, and two edge wires (`S27`/`E27`) near the border | 0 | 0 | 7 |

**Read:**
- **116 of 120 designs (97 %) can now be checked at bitstream level, and all of them prove.** No nextpnr, yosys or `gowin_pack` bug was shown in this design space.
- The value is the completed checker: four decode fixes to `gowin_unpack`, each tested against the fuzz corpus with two oracles. They're upstream contribution candidates.
- **Caveat:** the `ALU_RAW` semantics come from Apicula's doc and agree with yosys's model. Neither has been checked on hardware.
- **Two more checker guards added on the way:**
  - a **logic loop** in the output fan-in now counts as a gap;
  - a planted-loop control proves the check fires.
  - The 582 iCE40 netlists were re-audited: no loops, no undriven fan-in.

## Bugs found, in order (most were mine)
1. **`--noalu` misuse.** `gowin_unpack --noalu` *deletes* ALU cells and their carry chains; it is not a LUT-only decode.
2. **Constant-1 LUTs dropped by `gowin_unpack`** (Apicula issue draft 1): reproduced with a minimal two-LUT design. Fixed locally by `unpack_full.py`.
3. **`RAW_ALU_LUT` ALUs decoded as C2L/mode 9** (Apicula issue draft 2): reproduced with a minimal adder. Classified as `unpack_limit`.
4. **My proof flow gave false counterexamples.** It read both sides into one design without per-side `hierarchy -top`, so parameterised Gowin cells were not elaborated correctly. Found by simulating a "failing" LUT6 netlist, which iverilog and yosys `eval` both computed correctly. Fixed, and since then every design also runs the exhaustive-simulation oracle.
   - The iCE40 checker is not affected: `icebox_vlog` writes plain assigns and no parameterised cells.
5. **Long-wire taps (`LT` → `LBO`/`LB`) are not decoded by `gowin_unpack`** (note 3), which leaves LUT inputs floating. Detected automatically (`undriven_fanin`).
6. **One `-noalu` multiply blew up synthesis** (more than 9 minutes). There is now a 180 s step timeout, counted as a flow error.
7. **A SAT "proof" over undriven nets is unsound** (seed 2, ALU run). Found only because the exhaustive simulation disagreed. Fixed: undriven fan-in now overrides any SAT verdict, here and in the iCE40 prover.

## Limits
- 8-input combinational designs only, one device, one seed per design.
- The bitstream itself is never checked on hardware. All findings are about the *recovered* netlist.
- `unpack_limit` and `unpack_gap` designs say nothing either way about nextpnr or `gowin_pack`.

```bash
python3 gowinfuzz/gowinfuzz.py 60 work-noalu noalu
```
```bash
python3 gowinfuzz/gowinfuzz.py 60 work-alu alu
```

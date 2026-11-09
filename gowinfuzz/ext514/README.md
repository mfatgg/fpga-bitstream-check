# ext514: bitstream-level check of the YosysHQ/apicula#514 reproducers (2026-11-09)

**Question:** [apicula#514](https://github.com/YosysHQ/apicula/issues/514) reports designs whose carry chains compute wrong on a Tang Nano 20K (GW2AR-18). One cause was fixed in nextpnr #1736 (June 2026). An 8-port variant still failed afterwards (5/10 seeds; the same bitstream sometimes passes, sometimes fails). Does the bitstream itself compute what synthesis intended?

**Input:** `kit/` is the reporter's refreshed `alu_repro_kit.zip` (CC0, from the issue, sha256 `23b8522d…41038`). Only its Verilog and `.cst` files are used; its Python host scripts were not run.

**Method:** `../regcorr.py` (register correspondence with carry-cell cut points).
- The synthesized netlist (yosys `synth_gowin -family gw2a -nodsp`) is the reference.
- The bitstream side is `gowin_pack` → `../unpack_full.py`.
- Every flip-flop and every synthesized carry cell (ALU) is paired with its twin through nextpnr's placement record (`NEXTPNR_BEL`).
- Both sides are cut at those points, and yosys `equiv_make`/`equiv_simple` proves every register input and every ALU SUM/COUT output.
- An unproven point names one cell.

**Negative controls:** inverting one decoded ALU INIT, or flipping a single INIT bit (`mutant.py`), is caught at exactly that cell and nowhere else.

**Tools:**
- yosys 0.70+post (0c11ea098);
- Apycula 0.32 (`gowin_pack`, chip database);
- yowasp-nextpnr-himbaechel-gowin 0.11.1.0.post826 ("current"; includes #1736);
- 0.10.0.75.post792.dev0 ("pre-fix", uploaded 2026-05-27, before #1736), in a separate `.venv-old`.

The reporter used oss-cad-suite builds (nextpnr 0.10-10, and later 2b560ad0), so the bitstreams here are not byte-identical to theirs.

## Results

| Design | nextpnr | Seeds | Equivalence points | Unproven | Silicon (reporter) |
|---|---|---|---|---|---|
| `crtb_9699690` (8-port residual) | current | 1–10 | 8,779 each | **0** on every seed | fails 5/10 seeds after the fix, nondeterministically |
| `crtb_2310` (minimum failing) | pre-fix | 1–10 | 3,390 each | **7** on every seed | failed 10/10 seeds before the fix |
| `crtb_2310` | current | 1 | 3,390 | 0 | passes after the fix |
| `rad_min_uart` (full size) | pre-fix | 1 | 5,893 | **7** | failed 57/58 loads before the fix |
| `rad_min_uart` | current | 1 | 5,893 | 0 | passes after the fix |

**What the 7 unproven points are (same signature in every pre-fix build checked):**
- They are all **COUT** of carry cells in the weighted-sum adder (`s2_wsum`), always with raw LUT `0x00fc`, and nextpnr disconnected both operand inputs (I0 and I1) as constants.
- In synthesis these cells have constant operands, so their correct carry-out is a constant. The bitstream LUT `0x00fc` instead makes COUT equal to input B (`G = INIT[{B,A}]`).
- With nothing routed to B, the tile's input mux in its default state (as Apicula decodes it) feeds B from `F1`, a live LUT output in the same tile. So a stray carry is injected whenever that unrelated signal is 1, which shifts the sum by a power of two. That fits the reported symptom (weighted sum off by 2^k, data-dependent).
- The current nextpnr (#1736, `optimize_alu_lut` rewritten) gives 0 unproven points on the same designs.

## What this does and doesn't show
- **Shown (V3, own runs):**
  - The method automatically finds the bug class that #1736 fixed, pointing at the exact cells, with no hardware.
  - With the current nextpnr, the 8-port residual's bitstreams compute exactly the synthesized netlist on all 10 seeds, at every register and every carry-cell output.
- **So the residual is most likely not a packing or LUT-init logic bug of this kind.** Candidates the check cannot see:
  - anything Apicula's chip database or the `doc/alu.md` ALU model describes differently from silicon (fuse meanings, unconfigured-mux defaults, carry-chain wiring);
  - timing or analog effects on long carry chains (the reporter saw load-to-load variation of one bitstream);
  - configuration loading.
- **Not shown:**
  - that the default `B ← F1` mux state is what the silicon does (that's Apicula's decode, and it fits the symptom);
  - anything for the reporter's exact toolchain builds.
- **Not checked:** top-level output pads (LED/UART pins), since the cut keeps only register and ALU points. They are downstream of the compared registers.

## Reproduce
```bash
ext514/check.sh crtb_2310 1 .venv-old
```
```bash
ext514/check.sh crtb_2310 1 .venv
```
`sweep.sh SEED` runs `crtb_9699690` with the current build. Build directories go to `ext514/b/` (about 1.9 GB, git-ignored).

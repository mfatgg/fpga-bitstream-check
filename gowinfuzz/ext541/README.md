# ext541: bitstream-level check of YosysHQ/apicula#541 (2026-11-09)

**Question:** [apicula#541](https://github.com/YosysHQ/apicula/issues/541) reports that the same netlist works with nextpnr seed 4242 and miscomputes with seed 12345 on a Tang Nano 20K (GW2A-18C). A signed comparison in a microcode branch is evaluated wrong; each bitstream is deterministic. The reporter's toolchain was nextpnr-himbaechel 0.11.1-8-g7c0c1c40, with Apycula 0.32/0.33. What differs between the two bitstreams?

**Input:** the reporter's own `gw2a18c-prebuilt.tar.gz`: `synth.json`, `pnr_{12345,4242}.json` and `design_{12345,4242}.fs`, sha256 `aa7eb509…7835`. These are their **exact bitstreams**, not rebuilt. Sources: `gw2a18c-repro-sources.tar.gz` (extracted to `src/`, git-ignored; not used for the check). **No licence is stated for the #541 package, so it is not included here:** download both archives from the issue.

**Method:** `../regcorr.py … alus drop=SPX9,SP,BSRAM,RAM16SDP4`:
- flip-flops and synthesized ALUs are paired through `NEXTPNR_BEL`, cut and proven with yosys `equiv_simple`;
- the 22 BSRAMs and 20 LUT-RAMs are not paired yet; they are removed on both sides, so every point they feed stays unproven on both seeds;
- undriven nets are free per side (`setundef -anyseq`).

**Soundness fix found here:** before that `setundef`, a net undriven on *both* sides counted as equal (yosys sets both to undef). With memories removed, that made memory-fed points "proven". The fix is now in regcorr, and the #514 results were re-run with it, unchanged.

## Results

| Build | Equivalence points | Unproven | Unproven only in this build |
|---|---|---|---|
| `design_12345.fs` (fails on hardware) | 2,694 | 308 names | `a201`, `a410` (SUM and COUT) |
| `design_4242.fs` (correct on hardware) | 2,694 | 308 names | `a223`, `a399` |
| 12345 with the 10 heads patched to `0x000a` | 2,694 | 294 | none |
| 4242 with the 10 heads patched to `0x000a` | 2,694 | 294 | none; **identical set to patched 12345** |

**Cause of the seed-specific points:** each of `a201`, `a410`, `a223` and `a399` is fed by a nextpnr carry-from-logic head cell (`*_HEAD_ALULC`, `RAW_ALU_LUT 0x505a`, no CIN connection). With `0x505a`, `COUT = I0 | CIN`, so the head passes on whatever reaches its CIN pin. In hardware, an ALU0's CIN is the dedicated carry wire from the tile to the west.
- **Failing build:** the head at `X8Y7/ALU0` (unpacked `R8C9_ALU_0`) has **another, unrelated carry chain** ending in the west tile (`R8C8_ALU_0…5`). That chain's carry leaks into `a201`, which is part of `tm.ctrl.hotstate_inst.Microcode.states` (the state/branch logic).
- **The other three cases:** nothing drives the west carry wire, so the head's CIN is unconnected and its value is undefined (probably 0 in silicon, so likely harmless).
- **Both builds have 10 such heads.** Which ones see a live neighbour depends on placement, which explains the seed dependence.

This is the bug fixed upstream in nextpnr **6030081a15 (2026-09-13)**, where the head becomes `0x000a`, ignoring CIN. The reporter's build (0.11.1-8, issue filed 2026-09-14) and the newest `yowasp-nextpnr-himbaechel-gowin` on PyPI (0.11.1.0.post826) both predate it. Our overnight fuzzer had flagged the same pattern as `nextpnr_unconnected_cin`.

## What this does and doesn't show
- **Shown (V3, own runs on the reporter's files):**
  - In the failing bitstream, a carry from an unrelated chain enters the microcode state logic.
  - Patching only the 10 head LUTs to the upstream value removes every seed-specific difference.
- **Not shown:**
  - that this is the comparison the reporter traced (that needs mapping `a201`'s cone to `__cmp_3`);
  - anything inside the memory-fed cones (294 points unchecked on both seeds);
  - hardware behaviour.
- **Decisive test (theirs):** flash `b/12345fix/design.fs`. It is the reporter's placement, with only the 10 head LUTs changed, packed with Apycula 0.32. Or rebuild with nextpnr ≥ 6030081a15.

## Reproduce
```bash
python3 regcorr.py ext541/pre/gw2a18c-prebuilt/synth.json ext541/pre/gw2a18c-prebuilt/pnr_12345.json ext541/b/12345/unpacked.v ext541/b/12345/rc alus drop=SPX9,SP,BSRAM,RAM16SDP4
```
(`unpacked.v` comes from `unpack_full.py -d GW2A-18C -o … design_12345.fs`. `b/` is git-ignored.)

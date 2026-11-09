# pldxfer: PAL/GAL logic transplanted to iCE40, equivalence proven on the shipped bitstream

**Question:** can a replacement for obsolete programmable logic come with a *complete* equivalence proof, not just test coverage? The proof should be checked against the image you actually ship, not the source.

## Flow
```
reference (sum-of-products: the form a PAL/GAL JEDEC fuse map decodes to)
  → yosys synth_ice40 → nextpnr-ice40 (UP5K) → icepack     = shipped .bin
  → iceunpack → icebox_vlog                                = netlist recovered from the .bin
  → yosys miter + SAT:
      combinational: proof over all 2^8 input combinations (SAT, not sampling)
      registered:    bounded check from power-up, depth 20
  → negative control: flip one literal in one product term (one wrong fuse); it must FAIL
```

## Results (2026-11-06, V3 on the tool chain)
| Design | Product terms | Shipped image | Proof | Negative control |
|---|---|---|---|---|
| `gal16v8_decoder`: 8 inputs, 8 combinational outputs (GAL16V8-style) | 43 | 104,090 B | **Proved** (SAT: no counterexample) | **Fails** with a counterexample ✔ |
| `pal16r4_fsm`: 4 registered outputs with feedback + 4 combinational outputs (PAL16R4-style) | 38 | 104,090 B | **Proved** to depth 20; **complete** because both sides have exactly 4 flip-flops and all of them are observed outputs | **Fails** with a counterexample ✔ |

**Why the registered proof is complete here:**
- In a PAL16R4 each register *is* an output pin, and the recovered netlist has no extra flip-flops (4 vs 4, counted by `count_ffs`).
- So equal outputs pin down the product state, and any mismatch must appear within 2⁴ + 1 = 17 steps.
- Assumption: each gate flip-flop maps to an observed registered output. That holds for this design; a general tool must check the mapping structurally. Designs with buried state (GAL22V10 with feedback-only registers, or CPLD macrocells) need induction or a state-mapping step.

An attempt with yosys temporal induction (`sat -tempinduct`) did not finish within about 10 minutes and was stopped.

## Not done (honest)
- **No JEDEC fuse-map decoder.** The reference is generated as sum-of-products. Decoding real GAL16V8/22V10 or ATF150x JEDs is a known, solved step (MAME `jedutil`, galasm; prjbureau for ATF15xx), but it is not integrated or validated here.
- **No electrical or timing equivalence:** 5 V tolerance, pin-to-pin delay, power-up state of the real part, tri-state/OE behaviour.
- **Nothing run on hardware.**
- **Protected parts:** a design read from a security-fused part is out of scope: use only JEDs you own or unprotected parts.

```bash
python3 pldxfer/pldxfer.py
```
```bash
python3 -m unittest discover -s pldxfer/tests
```

## Correction (2026-11-07)
The first version built the miter with `-ignore_gold_x`. Without undef modelling, that let SAT treat every reference bit that is 0 as "don't care", **so only the 1-bits were checked**.
- **Found by:** a negative control in [flowfuzz](../flowfuzz/README.md). A design computing `~a` "matched" a constant-0 design.
- **Fix:** the flag is removed.
- **Re-run:** both proofs still hold, and both one-fuse mutants still fail with counterexamples. The results above stand, now under the strict check.
- **Regression:** flowfuzz runs that exact pair as a self-test before every fuzz run.

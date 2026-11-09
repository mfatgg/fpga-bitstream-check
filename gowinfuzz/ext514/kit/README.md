# GW2A inferred-ALU miscompute: instrumented reproducer kit

Everything here is CC0. Board: Sipeed Tang Nano 20K (GW2AR-LV18QN88C8/I7),
27 MHz oscillator. The designs self-report their verdict + diagnostics over
the board's own USB COM port (FPGA pin 69 -> onboard BL616 -> 115200 8N1),
so no LED watching and no extra wiring is needed.

## Files

- `rad_min_uart.v` -- the full-size reproducer (7-port CRT roundtrip,
  N=510510) plus instrumentation strictly downstream of the compare:
  first-fail capture (counter, wrong reconstruction, pre-reduction weighted
  sum), a second capture keyed to a different residue class, per-channel
  failure bitmaps, and a UART streamer that repeats one 64-char verdict
  line forever. A BUILD_ID parameter (set per build via `chparam`) is
  echoed in the line -- a verdict carrying the wrong ID means the chip is
  still running the PREVIOUS configuration (see "silent reconfiguration
  failures" in the issue comment; we catch these regularly).
- `rad_min_uart_tb.v` -- testbench. Default mode: full sweep must pass and
  the decoded UART line must be the all-zero P line. `-DCORRUPT` corrupts
  one weight and checks the exact expected failure line (captures, bitmaps,
  error count all hand/golden-computed) -- certifies the instrumentation.
- `rad_min_uart.cst` -- pins (clock, 6 LEDs, pin 69 UART).
- `seed_sweep.py` -- unattended build -> flash -> read-verdict loop.
  Decodes captures and bitmaps, names the culprit channel, prints each
  bitstream's sha256 and the yosys cell census. Needs pyserial.
- `gen_bisect.py` -- emits variants of the same architecture for any
  subset of {2,3,5,7,11,13,17,19,23}: design + testbench (with
  golden-model EXPECT lines, both modes) + a JSON spec that
  seed_sweep.py consumes (report field widths scale with the variant;
  the parser follows the spec). Refuses to emit any variant whose
  Python golden model does not roundtrip exhaustively.
- `crtb_<N>.v/_tb.v/.json` -- the generated ladder we ran: N = 30030,
  72930, 2310, 210, 105, 35 (all testbench modes verilator-green;
  original-bug silicon verdicts in the issue comment: minimum failing
  crtb_2310, 5 ports, 834 ALU cells; crtb_210 and below pass), plus
  crtb_9699690 (8 ports) -- the RESIDUAL-AFTER-FIX reproducer: on
  nextpnr 2b560ad0 (which fixes every 7-port-and-below member) it
  fails 5/10 single-load seed sweeps with 1-3 bad counter values
  each, always counters with one or two high bits set (0x400000,
  0x600000, 0x800000 observed), weighted sum off by small multiples
  of the mod-5 channel's CRT weight, reduction faithful downstream;
  re-loading a passing seed's identical bitstream (same sha256)
  flipped it to FAIL -- the per-load nondeterministic component from
  the original report survives.

## Usage

```sh
# full-size reproducer, ALU inference on (known bad), 20 seeds:
python seed_sweep.py --seeds 1-20 --port COM7
# same RTL, -noalu control (known good):
python seed_sweep.py --seeds 1 --noalu --port COM7
# extra synthesis flags get their own BUILD_ID so verdicts self-identify:
python seed_sweep.py --seeds 1 --synth-flags=-nowidelut --build-id b3 --port COM7
# a bisection variant:
python seed_sweep.py --spec crtb_2310.json --seeds 1-10 --port COM7
# regenerate / extend the variant family (e.g. a 5-port without 3):
python gen_bisect.py 2,5,7,11,13
```

Simulation (oss-cad-suite): compile design + tb under iverilog or
verilator; `-DCORRUPT` selects the corrupt mode. The tb decodes the actual
serial bit stream, not internal registers.

Toolchains: original failures on yosys 0.63+161 / nextpnr-himbaechel
0.10-10 / apycula 0.30.dev11 (oss-cad-suite bundle, SRAM loads); fix
verification AND the 8-port residual on the oss-cad-suite 2026-07-01
nightly (nextpnr 0.10-82-g2b560ad0).

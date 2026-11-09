# fpga-bitstream-check

Equivalence checks for **open FPGA flows at bitstream level**: recover the netlist from the final bitstream and prove it computes what synthesis produced. Every proof comes with negative controls, because an equivalence "proof" without them can be vacuous.

| Folder | What |
|---|---|
| [`flowfuzz/`](flowfuzz/README.md) | Lattice iCE40 (yosys → nextpnr-ice40 → icepack): random-design fuzzing with output, LUT, routing and carry-bit mutants |
| [`pldxfer/`](pldxfer/README.md) | The shared prover (yosys miter + SAT, with undriven-net, logic-loop and port-list guards); PAL/GAL logic transplanted to iCE40 |
| [`gowinfuzz/`](gowinfuzz/README.md) | Gowin GW1N/GW2A (yosys → nextpnr-himbaechel → Apicula): `unpack_full.py` completes `gowin_unpack` (see YosysHQ/apicula#583–#586); `regcorr.py` does register/carry-cell correspondence for large designs |
| [`gowinfuzz/ext514/`](gowinfuzz/ext514/README.md) | YosysHQ/apicula#514 reproducers (CC0 kit): the pre-#1736 nextpnr bug found at the exact cells; the 8-port residual proves on 10/10 seeds |
| [`gowinfuzz/ext541/`](gowinfuzz/ext541/README.md) | YosysHQ/apicula#541 write-up (the reporter's files aren't included: get them from the issue) |

Write-ups: [fuzzing the open iCE40 flow](https://asknau.com/en/tech-blog/2026/11/07/fuzzing-the-open-ice40-flow/), [two Gowin miscompute reports explained from the bitstream](https://asknau.com/en/tech-blog/2026/11/09/checking-gowin-bitstreams/).

## Requirements
- Python 3.11+ (standard library only for the core);
- yosys, nextpnr-ice40, icestorm (icepack, iceunpack, icebox_vlog), iverilog.
- **Gowin:** a venv at `gowinfuzz/.venv` with `pip install apycula yowasp-nextpnr-himbaechel-gowin`. Tested with Apycula 0.32, yowasp-nextpnr-himbaechel-gowin 0.11.1.0.post826 and yosys 0.70.

## Quick start
```bash
python3 -m unittest discover -s pldxfer/tests
```
```bash
python3 flowfuzz/flowfuzz.py 20
```
```bash
gowinfuzz/.venv/bin/python -m unittest discover -s gowinfuzz/tests
```
```bash
python3 gowinfuzz/regcorr.py SYNTH.json PNR.json UNPACKED.v WORKDIR alus
```
(`UNPACKED.v` comes from `gowinfuzz/.venv/bin/python gowinfuzz/unpack_full.py -d GW2A-18C -o UNPACKED.v design.fs`.)

## Limits
- Checks are against the chip model of the open flow (Project IceStorm, Apicula), not silicon.
- Block RAM and DSP contents aren't compared yet.
- `regcorr` assumes nextpnr's placement record (`NEXTPNR_BEL`) pairs the cells. An unproven point means "differs or unprovable here", never "proven wrong" without a counterexample.

Questions or a bitstream to check: [asknau.com/en/services/bitstream-check](https://asknau.com/en/services/bitstream-check/) · issues welcome.

MIT licence. `gowinfuzz/ext514/kit/` is the CC0 reproducer kit from YosysHQ/apicula#514 by its reporter.

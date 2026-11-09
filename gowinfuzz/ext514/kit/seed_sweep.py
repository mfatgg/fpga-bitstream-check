"""seed_sweep.py -- the upstream-ALU-bug placement + load sweep (P49).

Builds rad_min_uart.v with ALU inference ON (the known-bad config,
HARDWARE.md silicon rule 1), place-and-routes it under a series of nextpnr
seeds, flashes each bitstream to the Tang Nano 20K over SRAM (optionally
N times each -- the P49 re-flash of seed 1 flipped PASS -> FAIL on an
identical bitstream, so per-LOAD variance is part of the bug), and reads
the design's UART self-report from the board's COM port -- no human LED
observation in the loop. Per failing load it decodes:
  capture1 / capture2 -- first failing counter (+ first with a different
    cnt%7), wrong reconstruction, pre-reduction weighted sum -> macc-wrong
    vs reduction-wrong, exact diff;
  per-channel failure bitmaps -- the culprit channel's residue set stays a
    proper subset; innocent channels saturate.
Every bitstream's sha256 is printed: same seed -> same hash = toolchain
determinism witnessed in the log itself.

Usage:
  python seed_sweep.py [--seeds 1-20] [--repeat 1] [--port COM7]
                       [--noalu] [--synth-only]
                       [--spec crtb_30030.json]   # bisection variant (P70):
                       #   gen_bisect.py emits design + spec; the spec swaps
                       #   in that variant's SRC/PRIMES/E/CHAIN/BUILD_IDs
                       [--synth-flags "-nowidelut"] [--build-id b3]

Sequential by design (RESOURCE SAFETY): one toolchain process at a time,
serial port strictly closed around openFPGALoader (the BL616 cannot flash
while the port is open -- Sipeed uart example docs).
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path

import serial

HERE = Path(__file__).resolve().parent
# OSS_CAD env var overrides (P87: test candidate toolchains -- e.g. a nightly
# carrying the upstream ALU fix -- without touching the working install).
OSS_CAD = os.environ.get("OSS_CAD", "C:\\Soft\\oss-cad-suite")
DEVICE = "GW2AR-LV18QN88C8/I7"
FAMILY = "GW2A-18C"
CST = "rad_min_uart.cst"
SRC = "rad_min_uart.v"
BAUD = 115200
def make_line_re(dcnt=5, dw=6, dbm=15):
    """Report-line parser; field widths come from the variant spec (P88 --
    variants above rad scale emit wider counter/wsum/bitmap fields)."""
    g = "([0-9a-f]{%d})"
    return re.compile(r"^([PF]) " + " ".join(
        g % d for d in (5, dcnt, dcnt, dw, dcnt, dcnt, dw, dbm, 2)) + "$")


LINE_RE = make_line_re()
BUILD_ID = {"alu": "a1", "noalu": "5c"}
KEY = 7   # the capture-2 keying channel (spec overrides)

# CRT constants (must match rad_min_uart.v); a --spec file overrides all of
# these module-level defaults with its variant's values.
PRIMES = [2, 3, 5, 7, 11, 13, 17]
E = {2: 255255, 3: 170170, 5: 306306, 7: 145860, 11: 46410, 13: 157080, 17: 450450}
CHAIN = (8168160, 4084080, 2042040, 1021020, 510510)
RECON_BITS = 19
SYNTH_FLAGS = ""


def toolchain(cmdline, timeout=600):
    """Run a toolchain command through cmd.exe + environment.bat (the
    build_fpga.sh pattern -- the tools resolve DLLs only that way)."""
    full = f'call {OSS_CAD}\\environment.bat && cd /d {HERE} && {cmdline}'
    r = subprocess.run(["cmd.exe", "/c", full], capture_output=True,
                       text=True, timeout=timeout)
    if r.returncode != 0:
        print(r.stdout[-3000:])
        print(r.stderr[-3000:])
        raise RuntimeError(f"toolchain step failed: {cmdline.split()[0]}")
    return r.stdout


def synth(alu: bool) -> str:
    tag = "alu" if alu else "noalu"
    js = f"_sweep_{tag}.json"
    ys = HERE / f"_sweep_{tag}.ys"
    ylog = HERE / "_sweep_yosys.log"
    flag = ("" if alu else " -noalu") + (f" {SYNTH_FLAGS}" if SYNTH_FLAGS else "")
    bid = int(BUILD_ID[tag], 16)
    ys.write_text(f"read_verilog {SRC}\n"
                  f"chparam -set BUILD_ID {bid} top\n"
                  f"synth_gowin -top top -family gw2a -nodsp{flag} -json {js}\n"
                  f"stat\n",
                  encoding="ascii")
    print(f"[synth] yosys ({tag}{flag}, id {BUILD_ID[tag]}) ...", flush=True)
    toolchain(f"yosys -q -l {ylog.name} {ys.name}")
    ys.unlink()
    # cell census from the final `stat` (the minimum-failing-size metric)
    cells = {m[1]: int(m[0]) for m in
             re.findall(r"^\s+(\d+)\s+(\w+)\s*$", ylog.read_text(), re.MULTILINE)}
    census = " ".join(f"{k}={cells[k]}" for k in
                      ("ALU", "LUT1", "LUT2", "LUT3", "LUT4", "MUX2_LUT5")
                      if k in cells)
    dffs = sum(n for k, n in cells.items() if k.startswith("DFF"))
    print(f"[synth] cells: {census} DFF*={dffs}", flush=True)
    return js


def build(js: str, seed: int, fs: str = "_sweep.fs"):
    """nextpnr + pack. Returns (fmax, sha256 of the bitstream)."""
    print(f"[seed {seed}] nextpnr ...", flush=True)
    toolchain(
        f"nextpnr-himbaechel --json {js} --write _sweep_pnr.json "
        f"--report _sweep_report.json --seed {seed} --device {DEVICE} "
        f"--vopt cst={CST} --vopt family={FAMILY} -q")
    rep = json.loads((HERE / "_sweep_report.json").read_text())
    fmax = min(d["achieved"] for d in rep.get("fmax", {}).values()) if rep.get("fmax") else 0.0
    toolchain(f"gowin_pack -d {FAMILY} -o {fs} _sweep_pnr.json")
    digest = hashlib.sha256((HERE / fs).read_bytes()).hexdigest()[:12]
    print(f"[seed {seed}] built {fs}: Fmax {fmax:.1f} MHz, fs sha256 {digest}", flush=True)
    return fmax, digest


def flash(fs: str = "_sweep.fs"):
    toolchain(f"openFPGALoader -b tangnano20k {fs}", timeout=120)


def read_report(port: str, timeout_s: float = 25.0):
    """Read the self-report: two identical consecutive well-formed lines.
    The sweep itself takes ~19 ms + a ~155 ms line gap, so the verdict is
    there long before we connect."""
    s = serial.Serial()
    s.port = port
    s.baudrate = BAUD
    s.timeout = 2.0
    s.dtr = False          # before open() -- never toggle lines on connect
    s.rts = False
    s.open()
    try:
        s.reset_input_buffer()   # drop any backlog from the previous config
        prev, deadline = None, time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            raw = s.readline().decode("ascii", "replace").strip()
            m = LINE_RE.match(raw)
            if not m:
                continue
            cur = m.groups()
            if prev == cur:
                return cur, raw
            prev = cur
        raise RuntimeError(f"no stable report line on {port} in {timeout_s}s")
    finally:
        s.close()


def expected(cnt: int):
    wsum = sum((cnt % p) * E[p] for p in PRIMES)
    return wsum, reduce_chain(wsum)


def reduce_chain(wsum: int) -> int:
    x = wsum
    for c in CHAIN:
        if x >= c:
            x -= c
    return x & ((1 << RECON_BITS) - 1)   # the Verilog recon is a sliced wire


def diag(cnt: int, recon: int, wsum: int) -> str:
    exp_wsum, exp_recon = expected(cnt)
    if wsum != exp_wsum:
        # is the wrong sum at least reduced FAITHFULLY downstream?
        red = ("reduction faithful" if recon == reduce_chain(wsum)
               else f"REDUCTION ALSO WRONG: recon {recon} vs {reduce_chain(wsum)}")
        return (f"cnt={cnt} (r{KEY}={cnt % KEY}) MACC wsum {wsum:#08x} "
                f"vs {exp_wsum:#08x} (diff {wsum - exp_wsum:+d}; {red})")
    return (f"cnt={cnt} (r{KEY}={cnt % KEY}) macc ok, REDUCTION recon {recon} "
            f"vs {exp_recon} (diff {recon - exp_recon:+d})")


def bitmaps(packed: int) -> str:
    """Unpack {pad2, bm17, bm13, bm11, bm7, bm5, bm3, bm2} (LSB first);
    name culprits = channels whose failing-residue set is a proper subset."""
    out, culprits, off = [], [], 0
    for p in PRIMES:
        bm = (packed >> off) & ((1 << p) - 1)
        off += p
        residues = [r for r in range(p) if bm & (1 << r)]
        if len(residues) < p:
            culprits.append(f"ch{p}:{residues}")
        out.append(f"bm{p}={bm:0{(p + 3) // 4}x}")
    verdict = " CULPRIT " + ",".join(culprits) if culprits else " (all channels saturated)"
    return " ".join(out) + verdict


def parse_seeds(spec: str):
    out = []
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="1-20")
    ap.add_argument("--repeat", type=int, default=1, help="loads per bitstream")
    ap.add_argument("--port", default="COM7")
    ap.add_argument("--noalu", action="store_true")
    ap.add_argument("--prenoalu", action="store_true",
                    help="flash the -noalu build (and verify it reads P) "
                         "before EACH measured load -- prior-config-state "
                         "probe: the P49 anomalous PASS was the only ALU "
                         "load that followed a noalu config")
    ap.add_argument("--synth-only", action="store_true")
    ap.add_argument("--spec", help="gen_bisect.py variant spec (json)")
    ap.add_argument("--synth-flags", default="",
                    help='extra synth_gowin flags, e.g. "-nowidelut"')
    ap.add_argument("--build-id",
                    help="override this mode's BUILD_ID (2 hex digits) -- "
                         "REQUIRED with --synth-flags so the verdict line "
                         "names the variant it came from")
    args = ap.parse_args()

    global SRC, PRIMES, E, CHAIN, RECON_BITS, BUILD_ID, SYNTH_FLAGS, LINE_RE, KEY
    if args.spec:
        spec = json.loads((HERE / args.spec).read_text())
        SRC = spec["src"]
        PRIMES = spec["primes"]
        E = {int(p): w for p, w in spec["E"].items()}
        CHAIN = tuple(spec["chain"])
        RECON_BITS = spec["recon_bits"]
        BUILD_ID = spec["build_ids"]
        KEY = spec.get("key", KEY)
        d = spec.get("digits")
        if d:
            LINE_RE = make_line_re(d["cnt"], d["wsum"], d["bm"])
    SYNTH_FLAGS = args.synth_flags
    if args.build_id:
        BUILD_ID = dict(BUILD_ID)
        BUILD_ID["noalu" if args.noalu else "alu"] = args.build_id
    elif SYNTH_FLAGS:
        ap.error("--synth-flags needs --build-id")

    js = synth(alu=not args.noalu)
    if args.synth_only:
        return
    if args.prenoalu:
        js_ctrl = synth(alu=False)
        build(js_ctrl, 1, fs="_sweep_ctrl.fs")

    mode = "noalu" if args.noalu else "alu"
    tag = mode if not args.spec else f"{spec['tag']}-{mode}"
    if SYNTH_FLAGS:
        tag += SYNTH_FLAGS.replace(" ", "")
    results = []
    log = HERE / "seed_sweep_results.txt"
    with log.open("a", encoding="ascii") as f:
        for seed in parse_seeds(args.seeds):
            fmax, digest = build(js, seed)
            for rep in range(args.repeat):
                if args.prenoalu:
                    flash("_sweep_ctrl.fs")
                    time.sleep(1.0)
                    (pv, *prest), _ = read_report(args.port)
                    pid = prest[-1]
                    ptag = "" if pid == BUILD_ID["noalu"] else \
                        f" RECONFIG FAILURE (chip still runs build {pid})"
                    print(f"    [prior] noalu verdict {pv} id {pid}{ptag}", flush=True)
                    f.write(f"    prior noalu verdict {pv} id {pid}{ptag}\n")
                flash()
                time.sleep(1.0)        # board settles after SRAM load
                (v, errs, cnt, recon, wsum, cnt2, recon2, wsum2, bms, bid), raw = \
                    read_report(args.port)
                errs, cnt, recon, wsum, cnt2, recon2, wsum2 = (
                    int(x, 16) for x in (errs, cnt, recon, wsum, cnt2, recon2, wsum2))
                row = (f"{tag} seed={seed:3d} load={rep + 1} fs={digest} "
                       f"fmax={fmax:6.1f} {v} id={bid} errors={errs} raw[{raw}]")
                if bid != BUILD_ID[mode]:
                    row += (f"\n    RECONFIG FAILURE: expected build {BUILD_ID[mode]},"
                            f" chip still runs build {bid} -- verdict belongs to"
                            f" the PREVIOUS config, not this bitstream")
                elif v == "F":
                    row += (f"\n    cap1 {diag(cnt, recon, wsum)}"
                            f"\n    cap2 {diag(cnt2, recon2, wsum2)}"
                            f"\n    {bitmaps(int(bms, 16))}")
                print(row, flush=True)
                f.write(row + "\n")
                f.flush()
                results.append((seed, rep, v if bid == BUILD_ID[mode] else "R"))

    fails = sum(1 for _, _, v in results if v == "F")
    recons = sum(1 for _, _, v in results if v == "R")
    passes = len(results) - fails - recons
    # RECONFIG rows carry the PREVIOUS config's verdict -- never count them
    # as passes (P87: a masked reconfig row briefly minted a 0/20 claim).
    print(f"\n=== {tag}: {fails}/{len(results)} loads FAIL, {passes} pass"
          + (f", {recons} RECONFIG (no verdict)" if recons else "")
          + f" -> {log.name} ===")


if __name__ == "__main__":
    main()

"""gen_bisect.py -- emit smaller-primorial variants of the ALU-bug reproducer
(P70, upstream bisection: "minimum ALU size required for a failure").

For each requested prime subset {p_i} (of 2,3,5,7,11,13,17,19,23) this generates a
design that is ARCHITECTURALLY IDENTICAL to rad_min_uart.v -- counter sweep
0..N-1 (N = prod p_i), %p decompose, |set|-port residue x CRT-weight macc,
conditional-subtract reduction chain, equality check against the delayed
counter, first-fail captures, per-channel failure bitmaps, and the same
64-char UART self-report -- with every width, weight, and chain constant
re-derived for that N. Three files per subset:

  crtb_<N>.v       the design (module top; same ports, same cst)
  crtb_<N>_tb.v    testbench, PASS + -DCORRUPT modes; both EXPECT lines are
                   computed here by an exhaustive Python golden model, so a
                   sim PASS certifies the generated math end to end
  crtb_<N>.json    spec for seed_sweep.py --spec (primes, weights, chain,
                   recon width, per-variant BUILD_IDs -- a verdict line
                   always names the variant + flow it came from)

The generator refuses to emit a variant whose golden model does not
roundtrip exhaustively (all N counters) in Python first.

Report field widths (counter/recon, wsum, bitmap hex digits) are derived
per variant and recorded in the spec (P88 — the requalification found the
fixed toolchain still failing at 8 ports, so the ladder now extends ABOVE
rad scale: crtb_9699690); seed_sweep.py builds its line parser from the
spec, defaulting to the rad-scale widths.

Usage:
  python gen_bisect.py                      # the standard ladder (see LADDER)
  python gen_bisect.py 2,3,5,7 5,7          # explicit subsets
"""

import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ALL_PRIMES = [2, 3, 5, 7, 11, 13, 17, 19, 23]

# Descending channel-count ladder + the drop-7 probe (does the failure need
# the 7 channel, or just 6+ channels?).
LADDER = [
    [2, 3, 5, 7, 11, 13],      # N=30030  6-port, keeps 7
    [2, 3, 5, 11, 13, 17],     # N=72930  6-port, drops 7
    [2, 3, 5, 7, 11],          # N=2310   5-port
    [2, 3, 5, 7],              # N=210    4-port
    [3, 5, 7],                 # N=105    3-port
    [5, 7],                    # N=35     2-port
]

RESERVED_IDS = {"a1", "5c", "b3", "00"}   # rad alu/noalu, nowidelut, unset


def prod(xs):
    out = 1
    for x in xs:
        out *= x
    return out


def crt_weight(N, p):
    M = N // p
    return M * pow(M, -1, p)


def chain_for(N, maxsum):
    """Conditional-subtract constants N<<J .. N: exact mod for x < N<<(J+1)."""
    J = 0
    while (N << (J + 1)) <= maxsum:
        J += 1
    assert maxsum < (N << (J + 1))
    return [N << j for j in range(J, -1, -1)]


def reduce_chain(x, chain, recon_bits):
    for c in chain:
        if x >= c:
            x -= c
    return x & ((1 << recon_bits) - 1)


def golden(primes, E, chain, recon_bits, wsum_bits, corrupt_first):
    """Exhaustive sweep of the generated architecture, mirroring the Verilog
    bit-exactly (wsum wraps at its register width; the 20-bit error counter
    saturates at 0xFFFFF -- both only reachable in corrupt mode, the latter
    only above rad scale). Returns the report fields (all-zero when nothing
    fails)."""
    N = prod(primes)
    Ec = dict(E)
    if corrupt_first:
        Ec[primes[0]] += 1
    key = 7 if 7 in primes else max(primes)
    errors = 0
    cap = cap2 = None
    bms = {p: 0 for p in primes}
    for cnt in range(N):
        wsum = sum((cnt % p) * Ec[p] for p in primes) & ((1 << wsum_bits) - 1)
        recon = reduce_chain(wsum, chain, recon_bits)
        if recon != cnt:
            errors += 1
            for p in primes:
                bms[p] |= 1 << (cnt % p)
            if cap is None:
                cap = (cnt, recon, wsum)
            elif cap2 is None and (cnt % key) != (cap[0] % key):
                cap2 = (cnt, recon, wsum)
    cap = cap or (0, 0, 0)
    cap2 = cap2 or (0, 0, 0)
    packed, off = 0, 0
    for p in primes:
        packed |= bms[p] << off
        off += p
    return min(errors, 0xFFFFF), cap, cap2, packed


def report_line(verdict, errors, cap, cap2, packed, bid, dcnt=5, dw=6, dbm=15):
    return (f"{verdict} {errors:05x} {cap[0]:0{dcnt}x} {cap[1]:0{dcnt}x} {cap[2]:0{dw}x} "
            f"{cap2[0]:0{dcnt}x} {cap2[1]:0{dcnt}x} {cap2[2]:0{dw}x} {packed:0{dbm}x} {bid}")


def build_id(N, flow):
    h = hashlib.sha256(f"crtb{N}{flow}".encode()).hexdigest()[:2]
    assert h not in RESERVED_IDS, f"BUILD_ID collision for N={N} {flow}: {h}"
    return h


def gen_design(primes):
    N = prod(primes)
    E = {p: crt_weight(N, p) for p in primes}
    cb = (N - 1).bit_length()
    maxsum = sum((p - 1) * E[p] for p in primes)
    W = maxsum.bit_length()
    # Field widths FLOOR at rad scale (every variant at or below rad emits
    # the original 64-char line, byte-identical) and grow only above it (P88).
    dcnt = max(5, (cb + 3) // 4)      # cnt/recon hex digits
    dw = max(6, (W + 3) // 4)         # wsum hex digits
    sbm = sum(primes)                 # packed-bitmap bits
    dbm = max(15, (sbm + 3) // 4)     # bitmap hex digits
    nch = 1 + sum(1 + d for d in (5, dcnt, dcnt, dw, dcnt, dcnt, dw, dbm)) + 3
    total = nch + 2                   # + CR LF
    assert total <= 127, "UART idx register is 7 bits"
    chain = chain_for(N, maxsum)
    key = 7 if 7 in primes else max(primes)
    p1 = primes[0]
    rb = {p: (p - 1).bit_length() for p in primes}   # residue widths
    names = [f"r{p}" for p in primes]

    L = []
    a = L.append
    plist = ",".join(map(str, primes))
    a(f"// crtb_{N}.v -- GENERATED by gen_bisect.py (P70): {len(primes)}-port")
    a(f"// bisection variant of rad_min_uart.v, primes {{{plist}}}, N={N}.")
    a("// Same architecture, widths/weights/chain re-derived; same UART")
    a("// report format. Do not edit -- regenerate.")
    a("// CC0. Target: Tang Nano 20K (GW2AR-18, 27 MHz).")
    a("")
    a("module top #(")
    a(f"    parameter [{W-1}:0] P_E{p1} = {W}'d{E[p1]},  // tb corruption hook")
    a("    parameter GAP_BITS      = 22,")
    a("    parameter [7:0] BUILD_ID = 8'h00")
    a(")(")
    a("    input clk,")
    a("    output [5:0] led,")
    a("    output uart_tx")
    a(");")
    for p in primes[1:]:
        a(f"    localparam [{W-1}:0] E{p} = {W}'d{E[p]};")
    a("")
    a("    // Self-clearing reset")
    a("    reg [3:0] rstsr = 4'hF;")
    a("    always @(posedge clk) rstsr <= {rstsr[2:0], 1'b0};")
    a("    wire rst = rstsr[3];")
    a("")
    a(f"    reg [{cb-1}:0] cnt = 0;")
    a("    reg done = 0;")
    a(f"    wire cnt_last = (cnt == {cb}'d{N-1});")
    a("    always @(posedge clk) begin")
    a("        if (rst) begin cnt <= 0; done <= 0; end")
    a("        else if (!done) begin")
    a("            if (cnt_last) done <= 1;")
    a("            else cnt <= cnt + 1;")
    a("        end")
    a("    end")
    a("")
    a("    // Decompose")
    for p in primes:
        if p == 2:
            a("    wire [0:0] r2  = cnt[0];")
        else:
            a(f"    wire [{rb[p]-1}:0] r{p} = cnt % {p};")
    a("")
    for p in primes:
        a(f"    reg [{rb[p]-1}:0] s1_r{p};")
    a(f"    reg [{cb-1}:0] s1_cnt;")
    a("    always @(posedge clk) begin")
    for p in primes:
        a(f"        s1_r{p} <= r{p};")
    a("        s1_cnt <= cnt;")
    a("    end")
    a("")
    a(f"    // Reconstruct: {len(primes)}-port weighted sum + binary mod {N}")
    terms = [f"s1_r{p1} * P_E{p1}"] + [f"s1_r{p} * E{p}" for p in primes[1:]]
    a(f"    wire [{W-1}:0] wsum = " + " + ".join(terms) + ";")
    a("")
    prev = "wsum"
    for i, c in enumerate(chain[:-1]):
        q = f"q{len(chain)-1-i}"
        a(f"    wire [{W-1}:0] {q} = ({prev} >= {W}'d{c}) ? {prev} - {W}'d{c} : {prev};")
        prev = q
    a(f"    wire [{cb-1}:0] recon = ({prev} >= {W}'d{N}) ? "
      f"{prev}[{cb-1}:0] - {cb}'d{N} : {prev}[{cb-1}:0];")
    a("")
    a(f"    reg [{cb-1}:0] s2_recon, s2_cnt;")
    a(f"    reg [{W-1}:0] s2_wsum;")
    for p in primes:
        a(f"    reg [{rb[p]-1}:0] s2_r{p};")
    a("    always @(posedge clk) begin")
    a("        s2_recon <= recon;")
    a("        s2_cnt <= s1_cnt;")
    a("        s2_wsum <= wsum;")
    for p in primes:
        a(f"        s2_r{p} <= s1_r{p};")
    a("    end")
    a("")
    a("    wire step_ok = (s2_recon == s2_cnt);")
    a("    reg s3_error;")
    a("    always @(posedge clk) s3_error <= ~step_ok;")
    a("")
    a("    reg [2:0] done_sr = 0;")
    a("    always @(posedge clk) done_sr <= {done_sr[1:0], done};")
    a("")
    a("    reg [19:0] errors = 0;")
    a("    reg [2:0] warmup = 0;")
    a("    always @(posedge clk) begin")
    a("        if (rst) warmup <= 0;")
    a("        else if (warmup < 3'd5) warmup <= warmup + 1;")
    a("    end")
    a("    always @(posedge clk) begin")
    a("        if (rst) errors <= 0;")
    a("        else if (warmup >= 3'd3 && s3_error && !done_sr[2] && errors != 20'hFFFFF)")
    a("            errors <= errors + 1;")
    a("    end")
    a("")
    a("    wire fail_evt = (warmup >= 3'd2) && !step_ok && !done_sr[1];")
    a("")
    a("    reg captured = 0, captured2 = 0;")
    a(f"    reg [{cb-1}:0] cap_cnt = 0, cap_recon = 0, cap_cnt2 = 0, cap_recon2 = 0;")
    a(f"    reg [{W-1}:0] cap_wsum = 0, cap_wsum2 = 0;")
    a(f"    reg [{rb[key]-1}:0] cap_r{key} = 0;")
    a("    always @(posedge clk) begin")
    a("        if (rst) begin captured <= 0; captured2 <= 0; end")
    a("        else if (fail_evt) begin")
    a("            if (!captured) begin")
    a("                captured <= 1;")
    a("                cap_cnt <= s2_cnt; cap_recon <= s2_recon;")
    a(f"                cap_wsum <= s2_wsum; cap_r{key} <= s2_r{key};")
    a(f"            end else if (!captured2 && s2_r{key} != cap_r{key}) begin")
    a("                captured2 <= 1;")
    a("                cap_cnt2 <= s2_cnt; cap_recon2 <= s2_recon;")
    a("                cap_wsum2 <= s2_wsum;")
    a("            end")
    a("        end")
    a("    end")
    a("")
    a("    // Per-channel failure bitmaps")
    for p in primes:
        a(f"    reg [{p-1}:0] bm{p} = 0;")
    a("    always @(posedge clk) begin")
    a("        if (fail_evt) begin")
    for p in primes:
        a(f"            bm{p}[s2_r{p}] <= 1'b1;")
    a("        end")
    a("    end")
    a("")
    a("    // LED output (active low)")
    a("    reg [23:0] hb = 0;")
    a("    always @(posedge clk) hb <= hb + 1;")
    a("    wire pass = (errors == 0);")
    a("    wire [5:0] fail_leds = hb[23] ? 6'b101010 : 6'b010101;")
    a("    assign led = ~(done ? (pass ? 6'b111111 : fail_leds) : {5'b00000, hb[23]});")
    a("")
    a("    // UART self-report: 8N1, 115200 at 27 MHz; identical to rad_min_uart.v")
    a("    localparam [7:0] UART_DIV = 8'd234;")
    a("")
    a("    reg tx_line = 1'b1;")
    a("    reg [8:0] tx_shift = 0;")
    a("    reg [3:0] tx_bits = 0;")
    a("    reg [7:0] tx_div = 0;")
    a("    wire tx_busy = (tx_bits != 0);")
    a("    reg tx_start = 0;")
    a("    reg [7:0] tx_byte = 0;")
    a("    assign uart_tx = tx_line;")
    a("")
    a("    always @(posedge clk) begin")
    a("        if (!tx_busy) begin")
    a("            if (tx_start) begin")
    a("                tx_line <= 1'b0;")
    a("                tx_shift <= {1'b1, tx_byte};")
    a("                tx_bits <= 4'd10;")
    a("                tx_div <= 0;")
    a("            end")
    a("        end else if (tx_div == UART_DIV - 1) begin")
    a("            tx_div <= 0;")
    a("            tx_bits <= tx_bits - 1;")
    a("            if (tx_bits != 4'd1) begin")
    a("                tx_line <= tx_shift[0];")
    a("                tx_shift <= {1'b1, tx_shift[8:1]};")
    a("            end")
    a("        end else")
    a("            tx_div <= tx_div + 1;")
    a("    end")
    a("")
    a("    function [7:0] hexd(input [3:0] n);")
    a("        hexd = (n < 4'd10) ? (8'h30 + n) : (8'h57 + n);")
    a("    endfunction")
    for k in sorted({5, dcnt, dw, dbm}):
        a(f"    function [{8 * k - 1}:0] hex{k}(input [{4 * k - 1}:0] v);")
        a("        integer j;")
        a(f"        for (j = 0; j < {k}; j = j + 1) hex{k}[8*j +: 8] = hexd(v[4*j +: 4]);")
        a("    endfunction")
    a("")

    def field(digits, bits, name):
        p = 4 * digits - bits
        return (f"        8'h20, hex{digits}({{{p}'b0, {name}}}),"
                if p else f"        8'h20, hex{digits}({name}),")

    pad = 4 * dbm - sbm
    bmcat = ", ".join(f"bm{p}" for p in reversed(primes))
    bmpad = f"{pad}'b0, " if pad else ""
    a(f"    wire [{4 * dbm - 1}:0] bms = {{{bmpad}{bmcat}}};")
    a(f"    wire [{8 * total - 1}:0] msg = {{")
    a("        pass ? 8'h50 : 8'h46,             // 'P' / 'F'")
    a("        8'h20, hex5(errors),")
    a(field(dcnt, cb, "cap_cnt"))
    a(field(dcnt, cb, "cap_recon"))
    a(field(dw, W, "cap_wsum"))
    a(field(dcnt, cb, "cap_cnt2"))
    a(field(dcnt, cb, "cap_recon2"))
    a(field(dw, W, "cap_wsum2"))
    a(f"        8'h20, hex{dbm}(bms),")
    a("        8'h20, hexd(BUILD_ID[7:4]), hexd(BUILD_ID[3:0]),")
    a("        8'h0D, 8'h0A};")
    a("")
    a("    reg sending = 0;")
    a("    reg [6:0] idx = 0;")
    a(f"    reg [{8 * total - 1}:0] sh = 0;")
    a("    reg [GAP_BITS-1:0] gap = 0;")
    a("    always @(posedge clk) begin")
    a("        tx_start <= 0;")
    a("        if (!sending) begin")
    a("            if (done) begin")
    a("                gap <= gap + 1;")
    a("                if (&gap) begin sending <= 1; idx <= 0; sh <= msg; end")
    a("            end")
    a("        end else if (!tx_busy && !tx_start) begin")
    a(f"            tx_byte <= sh[{8 * total - 1}:{8 * total - 8}];")
    a(f"            sh <= {{sh[{8 * total - 9}:0], 8'h00}};")
    a("            tx_start <= 1;")
    a(f"            if (idx == 7'd{total - 1}) sending <= 0;")
    a("            else idx <= idx + 1;")
    a("        end")
    a("    end")
    a("endmodule")
    return N, E, cb, W, chain, key, (dcnt, dw, dbm, nch), "\n".join(L) + "\n"


def gen_tb(primes, N, E, chain, cb, W, bid_alu, digits):
    p1 = primes[0]
    dcnt, dw, dbm, nch = digits
    timeout = max(60_000_000, N * 74 + 40_000_000)
    errs, cap, cap2, packed = golden(primes, E, chain, cb, W, corrupt_first=True)
    exp_f = report_line("F", errs, cap, cap2, packed, bid_alu, dcnt, dw, dbm)
    exp_p = report_line("P", 0, (0, 0, 0), (0, 0, 0), 0, bid_alu, dcnt, dw, dbm)
    return f"""// crtb_{N}_tb.v -- GENERATED by gen_bisect.py. Two modes (see
// rad_min_uart_tb.v): default = full {N}-check sweep must PASS with the
// all-zero P line; -DCORRUPT corrupts P_E{p1} (+1) and the EXPECT line below
// (errors, both captures, bitmaps) was computed by the generator's exhaustive
// golden model -- a match certifies the generated math end to end.

`timescale 1ns / 1ps

module crtb_{N}_tb;
    reg clk = 0;
    wire [5:0] led;
    wire tx;

`ifdef CORRUPT
    top #(.P_E{p1}({E[p1] + 1}), .GAP_BITS(12), .BUILD_ID(8'h{bid_alu}))
        uut(.clk(clk), .led(led), .uart_tx(tx));
    localparam [{8 * nch - 1}:0] EXPECT =
        "{exp_f}";
`else
    top #(.GAP_BITS(12), .BUILD_ID(8'h{bid_alu}))
        uut(.clk(clk), .led(led), .uart_tx(tx));
    localparam [{8 * nch - 1}:0] EXPECT =
        "{exp_p}";
`endif
    localparam NCH = {nch};

    always #18.5 clk = ~clk;
    localparam real BIT = 234 * 37.0;

    reg [7:0] line [0:{nch + 1}];
    reg [7:0] rx_byte;
    reg [{8 * nch - 1}:0] got;
    integer i, b, ok;

    task recv_byte(output [7:0] data);
        begin
            @(negedge tx);
            #(BIT * 1.5);
            for (b = 0; b < 8; b = b + 1) begin
                data[b] = tx;
                #(BIT);
            end
            if (tx !== 1'b1) begin
                $display("=== FAILURE: bad stop bit ===");
                $finish;
            end
        end
    endtask

    initial begin
        $display("crtb_{N} -- {N}-check sweep + UART self-report");
        @(posedge uut.done);
        $display("sweep done, internal errors=%0d; decoding UART...", uut.errors);

        recv_byte(rx_byte);
        while (rx_byte != 8'h0A) recv_byte(rx_byte);
        for (i = 0; i < NCH + 2; i = i + 1) recv_byte(line[i]);

        ok = 1;
        for (i = 0; i < NCH; i = i + 1) begin
            got[8*(NCH-1-i) +: 8] = line[i];
            if (line[i] !== EXPECT[8*(NCH-1-i) +: 8]) ok = 0;
        end
        if (line[NCH] !== 8'h0D || line[NCH+1] !== 8'h0A) ok = 0;

        $display("uart line: '%s'", got);
        $display("expected:  '%s'", EXPECT);
        if (ok)
            $display("=== PASS: UART report matches expectation ===");
        else
            $display("=== FAILURE: UART report mismatch ===");
        $finish;
    end

    initial begin
        #{timeout:_};
        $display("TIMEOUT -- cnt: %0d, errors: %0d", uut.cnt, uut.errors);
        $finish;
    end
endmodule
"""


def main():
    sets = ([[int(x) for x in arg.split(",")] for arg in sys.argv[1:]]
            if len(sys.argv) > 1 else LADDER)
    seen_ids = set(RESERVED_IDS)
    for primes in sets:
        assert all(p in ALL_PRIMES for p in primes) and primes == sorted(primes)
        N, E, cb, W, chain, key, digits, design = gen_design(primes)

        # gate 1: the clean architecture must roundtrip exhaustively
        errs, _, _, _ = golden(primes, E, chain, cb, W, corrupt_first=False)
        assert errs == 0, f"N={N}: clean golden model has {errs} errors"

        bid_a, bid_n = build_id(N, "alu"), build_id(N, "noalu")
        assert bid_a not in seen_ids and bid_n not in seen_ids and bid_a != bid_n
        seen_ids |= {bid_a, bid_n}

        (HERE / f"crtb_{N}.v").write_text(design, encoding="ascii")
        (HERE / f"crtb_{N}_tb.v").write_text(
            gen_tb(primes, N, E, chain, cb, W, bid_a, digits), encoding="ascii")
        (HERE / f"crtb_{N}.json").write_text(json.dumps({
            "src": f"crtb_{N}.v",
            "tag": f"b{N}",
            "primes": primes,
            "key": key,
            "N": N,
            "E": {str(p): E[p] for p in primes},
            "chain": chain,
            "recon_bits": cb,
            "digits": {"cnt": digits[0], "wsum": digits[1], "bm": digits[2]},
            "build_ids": {"alu": bid_a, "noalu": bid_n},
        }, indent=2), encoding="ascii")
        print(f"crtb_{N}: primes {primes} cb={cb} W={W} digits={digits} "
              f"chain={chain} ids alu={bid_a} noalu={bid_n}")


if __name__ == "__main__":
    main()

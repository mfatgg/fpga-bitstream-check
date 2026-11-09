// rad_min_uart.v — rad_min.v + self-reporting (P49, upstream-bug seed sweep).
// The CRT roundtrip core is UNCHANGED from rad_min.v (decompose → s1 regs →
// 7-port macc → 24-bit conditional-subtract reduction → compare). Added,
// strictly DOWNSTREAM of the compare:
//   * first-fail capture — failing counter, wrong reconstruction, AND the
//     pre-reduction weighted sum (tells a wrong macc from a wrong reduction);
//   * second capture — first failure whose cnt%7 differs from the first
//     capture's (the P49 sweep found errors = 2/7 of all checks: exactly two
//     r7 residue classes fail; this fetches the second class's wrong value);
//   * per-channel failure bitmaps — which residue values of each channel
//     ever appear in a failing check; the culprit channel's bitmap stays a
//     proper subset, innocent channels fill up completely;
//   * a 115200-baud UART streamer (pin 69 → onboard BL616 → USB COM port)
//     repeating one 64-char ASCII line forever once the sweep is done:
//       "V errors cnt recon wsum cnt2 recon2 wsum2 bitmaps build_id"
//     (V = P/F; all fields hex: 5,5,5,6,5,5,6,15,2 digits; bitmaps packs
//     {pad2, bm17, bm13, bm11, bm7, bm5, bm3, bm2} = 60 bits) — so a host
//     script, not a human watching LEDs, reads the verdict. seed_sweep.py
//     is the driver + decoder.
// LED grammar unchanged (alu_repro.v protocol).
//
// P_E2 parameter: the tb corrupts it (+1) to force a KNOWN failure and
// verify captures, bitmaps, and UART end to end in simulation.
// Simulate with -v (verilator): bash sim_fpga.sh rad_min_uart.v -v
// CC0. Target: Tang Nano 20K (GW2AR-18, 27 MHz).

module top #(
    parameter [18:0] P_E2   = 19'd255255,  // tb corruption hook; default = real E2
    parameter GAP_BITS      = 22,          // report-line spacing ~155 ms; tb shrinks it
    parameter [7:0] BUILD_ID = 8'h00       // last line field (2 hex) -- set per build
                                           // (alu a1 / noalu 5c): a verdict carrying
                                           // the WRONG id = the chip is still running
                                           // the PREVIOUS config (silent reconfig
                                           // failure, the P49 anomaly suspect)
)(
    input clk,
    output [5:0] led,
    output uart_tx
);
    localparam [18:0] N_RAD = 19'd510510;
    localparam [18:0] E3  = 19'd170170;
    localparam [18:0] E5  = 19'd306306;
    localparam [18:0] E7  = 19'd145860;
    localparam [18:0] E11 = 19'd46410;
    localparam [18:0] E13 = 19'd157080;
    localparam [18:0] E17 = 19'd450450;

    // Self-clearing reset (see crt_rung9.v's note, P27)
    reg [3:0] rstsr = 4'hF;
    always @(posedge clk) rstsr <= {rstsr[2:0], 1'b0};
    wire rst = rstsr[3];

    reg [18:0] cnt = 0;
    reg done = 0;
    wire cnt_last = (cnt == 19'd510509);
    always @(posedge clk) begin
        if (rst) begin cnt <= 0; done <= 0; end
        else if (!done) begin
            if (cnt_last) done <= 1;
            else cnt <= cnt + 1;
        end
    end

    // Decompose
    wire [0:0] r2  = cnt[0];
    wire [1:0] r3  = cnt % 3;
    wire [2:0] r5  = cnt % 5;
    wire [2:0] r7  = cnt % 7;
    wire [3:0] r11 = cnt % 11;
    wire [3:0] r13 = cnt % 13;
    wire [4:0] r17 = cnt % 17;

    reg [0:0] s1_r2;  reg [1:0] s1_r3;  reg [2:0] s1_r5, s1_r7;
    reg [3:0] s1_r11, s1_r13; reg [4:0] s1_r17;
    reg [18:0] s1_cnt;
    always @(posedge clk) begin
        s1_r2 <= r2; s1_r3 <= r3; s1_r5 <= r5; s1_r7 <= r7;
        s1_r11 <= r11; s1_r13 <= r13; s1_r17 <= r17;
        s1_cnt <= cnt;
    end

    // Reconstruct: 7-port weighted sum + binary mod 510510
    wire [23:0] wsum = s1_r2 * P_E2 + s1_r3 * E3 + s1_r5 * E5 + s1_r7 * E7
                     + s1_r11 * E11 + s1_r13 * E13 + s1_r17 * E17;

    wire [23:0] q4 = (wsum >= 24'd8168160) ? wsum - 24'd8168160 : wsum;
    wire [23:0] q3 = (q4   >= 24'd4084080) ? q4   - 24'd4084080 : q4;
    wire [23:0] q2 = (q3   >= 24'd2042040) ? q3   - 24'd2042040 : q3;
    wire [23:0] q1 = (q2   >= 24'd1021020) ? q2   - 24'd1021020 : q2;
    wire [18:0] recon = (q1 >= 24'd510510)  ? q1[18:0] - 19'd510510 : q1[18:0];

    reg [18:0] s2_recon, s2_cnt;
    reg [23:0] s2_wsum;
    reg [0:0] s2_r2;  reg [1:0] s2_r3;  reg [2:0] s2_r5, s2_r7;
    reg [3:0] s2_r11, s2_r13; reg [4:0] s2_r17;
    always @(posedge clk) begin
        s2_recon <= recon;
        s2_cnt <= s1_cnt;
        s2_wsum <= wsum;
        s2_r2 <= s1_r2; s2_r3 <= s1_r3; s2_r5 <= s1_r5; s2_r7 <= s1_r7;
        s2_r11 <= s1_r11; s2_r13 <= s1_r13; s2_r17 <= s1_r17;
    end

    wire step_ok = (s2_recon == s2_cnt);
    reg s3_error;
    always @(posedge clk) s3_error <= ~step_ok;

    // done tail gate: each of the 510,510 checks participates exactly once;
    // the frozen end-state's eternal re-check of cnt=510509 is excluded
    // (done_sr[1] gates s2-stage events, done_sr[2] the s3-stage counter).
    reg [2:0] done_sr = 0;
    always @(posedge clk) done_sr <= {done_sr[1:0], done};

    // Error counter (rad pattern: verdict pipeline 3 deep, count from
    // warmup >= 3). Widened 8 → 20 bits vs rad_min.v: the TRUE count
    // (max 510510) is part of the failure signature.
    reg [19:0] errors = 0;
    reg [2:0] warmup = 0;
    always @(posedge clk) begin
        if (rst) warmup <= 0;
        else if (warmup < 3'd5) warmup <= warmup + 1;
    end
    always @(posedge clk) begin
        if (rst) errors <= 0;
        else if (warmup >= 3'd3 && s3_error && !done_sr[2] && errors != 20'hFFFFF)
            errors <= errors + 1;
    end

    // Failure events at the s2 compare. warmup >= 2 here selects the SAME
    // check set the error counter sees one cycle later at warmup >= 3 (the
    // tb's forced-corruption run verifies the alignment end to end).
    wire fail_evt = (warmup >= 3'd2) && !step_ok && !done_sr[1];

    reg captured = 0, captured2 = 0;
    reg [18:0] cap_cnt = 0, cap_recon = 0, cap_cnt2 = 0, cap_recon2 = 0;
    reg [23:0] cap_wsum = 0, cap_wsum2 = 0;
    reg [2:0] cap_r7 = 0;
    always @(posedge clk) begin
        if (rst) begin captured <= 0; captured2 <= 0; end
        else if (fail_evt) begin
            if (!captured) begin
                captured <= 1;
                cap_cnt <= s2_cnt; cap_recon <= s2_recon;
                cap_wsum <= s2_wsum; cap_r7 <= s2_r7;
            end else if (!captured2 && s2_r7 != cap_r7) begin
                captured2 <= 1;
                cap_cnt2 <= s2_cnt; cap_recon2 <= s2_recon;
                cap_wsum2 <= s2_wsum;
            end
        end
    end

    // Per-channel failure bitmaps
    reg [1:0]  bm2  = 0;
    reg [2:0]  bm3  = 0;
    reg [4:0]  bm5  = 0;
    reg [6:0]  bm7  = 0;
    reg [10:0] bm11 = 0;
    reg [12:0] bm13 = 0;
    reg [16:0] bm17 = 0;
    always @(posedge clk) begin
        if (fail_evt) begin
            bm2[s2_r2] <= 1'b1;  bm3[s2_r3] <= 1'b1;  bm5[s2_r5] <= 1'b1;
            bm7[s2_r7] <= 1'b1;  bm11[s2_r11] <= 1'b1;
            bm13[s2_r13] <= 1'b1; bm17[s2_r17] <= 1'b1;
        end
    end

    // LED output (active low), P27 grammar
    reg [23:0] hb = 0;
    always @(posedge clk) hb <= hb + 1;

    wire pass = (errors == 0);
    wire [5:0] fail_leds = hb[23] ? 6'b101010 : 6'b010101;
    assign led = ~(done ? (pass ? 6'b111111 : fail_leds) : {5'b00000, hb[23]});

    // ------------------------------------------------------------------
    // UART self-report: 8N1, 115200 at 27 MHz (divisor 234, +0.16% — in
    // spec). One 66-byte line (64 chars + CRLF), repeated with a
    // 2^GAP_BITS-cycle pause.
    // ------------------------------------------------------------------
    localparam [7:0] UART_DIV = 8'd234;

    reg tx_line = 1'b1;
    reg [8:0] tx_shift = 0;   // {stop, data[7:0]}
    reg [3:0] tx_bits = 0;
    reg [7:0] tx_div = 0;
    wire tx_busy = (tx_bits != 0);
    reg tx_start = 0;
    reg [7:0] tx_byte = 0;
    assign uart_tx = tx_line;

    always @(posedge clk) begin
        if (!tx_busy) begin
            if (tx_start) begin
                tx_line <= 1'b0;                     // start bit
                tx_shift <= {1'b1, tx_byte};         // data then stop
                tx_bits <= 4'd10;
                tx_div <= 0;
            end
        end else if (tx_div == UART_DIV - 1) begin
            tx_div <= 0;
            tx_bits <= tx_bits - 1;
            if (tx_bits != 4'd1) begin
                tx_line <= tx_shift[0];
                tx_shift <= {1'b1, tx_shift[8:1]};
            end
        end else
            tx_div <= tx_div + 1;
    end

    function [7:0] hexd(input [3:0] n);
        hexd = (n < 4'd10) ? (8'h30 + n) : (8'h57 + n);  // '0'-'9', 'a'-'f'
    endfunction

    function [39:0] hex5(input [19:0] v);
        integer j;
        for (j = 0; j < 5; j = j + 1) hex5[8*j +: 8] = hexd(v[4*j +: 4]);
    endfunction

    function [47:0] hex6(input [23:0] v);
        integer j;
        for (j = 0; j < 6; j = j + 1) hex6[8*j +: 8] = hexd(v[4*j +: 4]);
    endfunction

    function [119:0] hex15(input [59:0] v);
        integer j;
        for (j = 0; j < 15; j = j + 1) hex15[8*j +: 8] = hexd(v[4*j +: 4]);
    endfunction

    wire [59:0] bms = {2'b00, bm17, bm13, bm11, bm7, bm5, bm3, bm2};
    wire [527:0] msg = {
        pass ? 8'h50 : 8'h46,             // 'P' / 'F'
        8'h20, hex5(errors),
        8'h20, hex5({1'b0, cap_cnt}),
        8'h20, hex5({1'b0, cap_recon}),
        8'h20, hex6(cap_wsum),
        8'h20, hex5({1'b0, cap_cnt2}),
        8'h20, hex5({1'b0, cap_recon2}),
        8'h20, hex6(cap_wsum2),
        8'h20, hex15(bms),
        8'h20, hexd(BUILD_ID[7:4]), hexd(BUILD_ID[3:0]),
        8'h0D, 8'h0A};

    reg sending = 0;
    reg [6:0] idx = 0;
    reg [527:0] sh = 0;
    reg [GAP_BITS-1:0] gap = 0;
    always @(posedge clk) begin
        tx_start <= 0;
        if (!sending) begin
            if (done) begin
                gap <= gap + 1;
                if (&gap) begin sending <= 1; idx <= 0; sh <= msg; end
            end
        end else if (!tx_busy && !tx_start) begin
            tx_byte <= sh[527:520];
            sh <= {sh[519:0], 8'h00};
            tx_start <= 1;
            if (idx == 7'd65) sending <= 0;
            else idx <= idx + 1;
        end
    end
endmodule

// rad_min_uart_tb.v — testbench for rad_min_uart.v. Two modes:
//   default:   full 510,510-check sweep must PASS and the UART must report
//              the all-zero P line — decoded from the actual serial bit
//              stream, not peeked from registers.
//   -DCORRUPT: P_E2 is corrupted (+1), so every odd cnt fails. Expected,
//              all hand-computed: 255255 errors (3e517); capture1 cnt=1,
//              recon=2, wsum=175e8c (correct+1); capture2 = first failure
//              with a different r7 = cnt 3, recon 4, wsum 368746; bitmaps:
//              bm2 = {1} only (the culprit channel stays a proper subset),
//              every other channel saturates -> 3fffffffffffffe.
// Full sweep: use verilator. bash sim_fpga.sh rad_min_uart.v -v [-DCORRUPT]

`timescale 1ns / 1ps

module rad_min_uart_tb;
    reg clk = 0;
    wire [5:0] led;
    wire tx;

`ifdef CORRUPT
    top #(.P_E2(19'd255256), .GAP_BITS(12), .BUILD_ID(8'hA1))
        uut(.clk(clk), .led(led), .uart_tx(tx));
    localparam [511:0] EXPECT =
        "F 3e517 00001 00002 175e8c 00003 00004 368746 3fffffffffffffe a1";
`else
    top #(.GAP_BITS(12), .BUILD_ID(8'hA1))
        uut(.clk(clk), .led(led), .uart_tx(tx));
    localparam [511:0] EXPECT =
        "P 00000 00000 00000 000000 00000 00000 000000 000000000000000 a1";
`endif
    localparam NCH = 64;                // chars before CRLF

    always #18.5 clk = ~clk;            // ~27 MHz
    localparam real BIT = 234 * 37.0;   // UART bit period in ns

    reg [7:0] line [0:65];
    reg [7:0] rx_byte;
    reg [511:0] got;
    integer i, b, ok;

    task recv_byte(output [7:0] data);
        begin
            @(negedge tx);              // start-bit edge
            #(BIT * 1.5);               // middle of data bit 0
            for (b = 0; b < 8; b = b + 1) begin
                data[b] = tx;
                #(BIT);
            end
            if (tx !== 1'b1) begin      // now mid-stop-bit
                $display("=== FAILURE: bad stop bit ===");
                $finish;
            end
        end
    endtask

    initial begin
        $display("rad_min_uart -- 510510-check sweep + UART self-report");
        @(posedge uut.done);
        $display("sweep done, internal errors=%0d; decoding UART...", uut.errors);

        // sync: discard through the first end-of-line, then read a full line
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
        #60_000_000;
        $display("TIMEOUT -- cnt: %0d, errors: %0d", uut.cnt, uut.errors);
        $finish;
    end
endmodule

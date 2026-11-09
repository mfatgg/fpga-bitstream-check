// Exhaustive 256-vector comparison of fz (source) and gate (recovered netlist): a complete,
// SAT-independent second oracle for the 8-input combinational corpus (gowinfuzz.simulate).
module exh;
  reg [7:0] x; wire [7:0] g, r; integer k, bad;
  fz  ref_(.i0(x[0]),.i1(x[1]),.i2(x[2]),.i3(x[3]),.i4(x[4]),.i5(x[5]),.i6(x[6]),.i7(x[7]),
           .o0(r[0]),.o1(r[1]),.o2(r[2]),.o3(r[3]),.o4(r[4]),.o5(r[5]),.o6(r[6]),.o7(r[7]));
  gate dut(.i0(x[0]),.i1(x[1]),.i2(x[2]),.i3(x[3]),.i4(x[4]),.i5(x[5]),.i6(x[6]),.i7(x[7]),
           .o0(g[0]),.o1(g[1]),.o2(g[2]),.o3(g[3]),.o4(g[4]),.o5(g[5]),.o6(g[6]),.o7(g[7]));
  initial begin
    bad = 0;
    for (k = 0; k < 256; k = k + 1) begin x = k; #1; if (g !== r) begin bad = bad + 1; if (bad < 4) $display("DIFF x=%h ref=%h gate=%h", x, r, g); end end
    $display("EXH mismatches=%0d of 256", bad); $finish;
  end
endmodule

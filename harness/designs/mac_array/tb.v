`timescale 1ns/1ps
// Testbench gate for mac_array (ports are fixed; internals may change).
// Self-checking against a reference model with random, per-lane stimulus.
// Latency-tolerant: the design may add up to LAT-1 pipeline cycles on the
// accumulate path and on the sel->out path. Must hold full 24-bit wraparound
// semantics over long runs (2100 cycles).
module tb;
  localparam N = 4, ACC = 24, LAT = 6;
  reg clk = 0, rst_n = 0, en = 0, clear = 0;
  reg [4*N-1:0] a_flat = 0, b_flat = 0;
  reg [3:0] sel = 0;
  wire [ACC-1:0] out;
  reg signed [ACC-1:0] model [0:N*N-1];
  reg signed [3:0] av, bv;
  integer i, j, k, c, errors = 0, checks = 0, seed = 12345;

  mac_array #(.N(N), .ACC(ACC)) dut(.clk(clk), .rst_n(rst_n), .en(en), .clear(clear),
    .a_flat(a_flat), .b_flat(b_flat), .sel(sel), .out(out));

  always #5 clk = ~clk;
  initial begin #2000000; $display("TB FAIL (timeout)"); $finish; end

  task accumulate_model; begin
    for (i = 0; i < N; i = i + 1)
      for (j = 0; j < N; j = j + 1) begin
        av = a_flat[4*i +: 4]; bv = b_flat[4*j +: 4];
        model[i*N+j] = model[i*N+j] + av * bv;
      end
  end endtask

  task step_random; begin            // one cycle, random operands, random en
    a_flat = $random(seed); b_flat = $random(seed);
    en = (($random(seed) & 3) != 0);
    if (en) accumulate_model;
    @(negedge clk);
  end endtask

  task step_const(input [3:0] a, input [3:0] b); begin
    a_flat = {N{a}}; b_flat = {N{b}}; en = 1;
    accumulate_model;
    @(negedge clk);
  end endtask

  task clear_all; begin
    en = 0;
    @(negedge clk) clear = 1;
    @(negedge clk) clear = 0;
    repeat (LAT) @(negedge clk);
    for (k = 0; k < N*N; k = k + 1) model[k] = 0;
  end endtask

  task check_all; begin
    en = 0;
    repeat (LAT) @(negedge clk);
    for (k = 0; k < N*N; k = k + 1) begin
      sel = k;
      repeat (LAT) @(negedge clk);
      checks = checks + 1;
      if (out !== model[k]) begin
        errors = errors + 1;
        if (errors <= 8) $display("FAIL cell %0d got %0d want %0d", k, $signed(out), model[k]);
      end
    end
  end endtask

  initial begin
    for (k = 0; k < N*N; k = k + 1) model[k] = 0;
    repeat (3) @(negedge clk); rst_n = 1; @(negedge clk);
    check_all;                                             // reset -> zero
    clear_all; for (c = 0; c < 64;   c = c + 1) step_random;              check_all;
               for (c = 0; c < 64;   c = c + 1) step_random;              check_all;  // holds across reads
    clear_all; for (c = 0; c < 300;  c = c + 1) step_const(4'b1000, 4'b1000); check_all;  // -8*-8
    clear_all; for (c = 0; c < 2100; c = c + 1) step_const(4'b1000, 4'd7);    check_all;  // wide negative
    clear_all;                                             check_all;  // clear -> zero
    if (errors == 0) $display("TB PASS (%0d checks)", checks);
    else             $display("TB FAIL (%0d errors / %0d checks)", errors, checks);
    $finish;
  end
endmodule

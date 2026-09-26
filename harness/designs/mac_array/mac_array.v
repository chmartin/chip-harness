// 4x4 INT4 MAC array, 24-bit accumulators, registered output mux.
module mac_array #(parameter N = 4, parameter ACC = 24) (
  input  wire                      clk, rst_n, en, clear,
  input  wire [4*N-1:0]            a_flat, b_flat,
  input  wire [$clog2(N*N)-1:0]    sel,
  output reg  [ACC-1:0]            out
);
  wire [ACC*N*N-1:0] acc_all;
  genvar i, j;
  generate
    for (i = 0; i < N; i = i + 1) begin : row
      for (j = 0; j < N; j = j + 1) begin : col
        wire signed [3:0] a = a_flat[4*i +: 4];
        wire signed [3:0] b = b_flat[4*j +: 4];
        wire signed [7:0] p = a * b;
        reg  signed [ACC-1:0] acc;
        always @(posedge clk or negedge rst_n)
          if (!rst_n)      acc <= 0;
          else if (clear)  acc <= 0;
          else if (en)     acc <= acc + {{(ACC-8){p[7]}}, p};
        assign acc_all[ACC*(i*N+j) +: ACC] = acc;
      end
    end
  endgenerate
  always @(posedge clk) out <= acc_all[sel*ACC +: ACC];
endmodule

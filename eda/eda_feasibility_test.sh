#!/usr/bin/env bash
# EDA feasibility test for Apple Silicon (M1 Max). Decides: laptop vs x86 cloud VM.
# Runtime: ~20-40 min, mostly the one-time Docker image and OSS CAD Suite downloads.
#
# Before running:
#   1. Docker Desktop installed and running.
#   2. Docker Desktop > Settings > General > "Use Rosetta for x86_64/amd64 emulation" = ON.
#   3. Docker Desktop > Settings > Resources: CPUs >= 8, Memory >= 32 GB.
#
# Usage:  bash eda_feasibility_test.sh   (outputs go to ./eda-test next to this script)
# Overrides: ORFS_IMAGE=<image:tag>  ORFS_PLATFORM=linux/arm64 (if a native arm64 image exists)
set -euo pipefail

WORK="${EDA_WORK:-$(cd "$(dirname "$0")" && pwd)/eda-test}"
IMAGE="${ORFS_IMAGE:-openroad/orfs:latest}"
PLAT="${ORFS_PLATFORM:-linux/amd64}"
PAR="${PAR:-4}"   # parallel runs for the throughput test

mkdir -p "$WORK/mac" "$WORK/out"
cd "$WORK"
RESULTS="$WORK/results.txt"; : > "$RESULTS"
log()    { printf '\n== %s ==\n' "$*"; }
record() { echo "$*" | tee -a "$RESULTS"; }
now()    { date +%s; }

# ---------------------------------------------------------------- 0. preflight
log "0. Preflight"
if [ "$(uname)" = Darwin ]; then CORES=$(sysctl -n hw.ncpu); MEMB=$(sysctl -n hw.memsize); OSS_ASSET=darwin-arm64
else CORES=$(nproc); MEMB=$(( $(awk '/MemTotal/{print $2}' /proc/meminfo) * 1024 )); OSS_ASSET=linux-x64; fi
record "arch: $(uname -m)  cores: $CORES  mem_gb: $(( MEMB / 1073741824 ))"
command -v docker >/dev/null || { echo "Docker not installed"; exit 1; }
docker info >/dev/null 2>&1 || { echo "Start Docker Desktop first"; exit 1; }
record "docker cpus: $(docker info --format '{{.NCPU}}')  docker mem_gb: $(( $(docker info --format '{{.MemTotal}}') / 1073741824 ))"

# ---------------------------------------------------------------- 1. test design
log "1. Writing 4x4 INT4 MAC array, testbench, SDC, ORFS config"
cat > mac/mac_array.v <<'EOF'
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
EOF

cat > mac/tb.v <<'EOF'
`timescale 1ns/1ps
module tb;
  localparam N = 4, ACC = 24;
  reg clk = 0, rst_n = 0, en = 0, clear = 0;
  reg [4*N-1:0] a_flat, b_flat;
  reg [3:0] sel = 0;
  wire [ACC-1:0] out;
  integer k, errors = 0;
  mac_array #(.N(N), .ACC(ACC)) dut(.clk(clk), .rst_n(rst_n), .en(en), .clear(clear),
    .a_flat(a_flat), .b_flat(b_flat), .sel(sel), .out(out));
  always #5 clk = ~clk;
  task run(input [3:0] av, input [3:0] bv, input integer cycles, input integer want);
    begin
      a_flat = {N{av}}; b_flat = {N{bv}};
      @(negedge clk) clear = 1;
      @(negedge clk) begin clear = 0; en = 1; end
      repeat (cycles) @(negedge clk);
      en = 0;
      for (k = 0; k < N*N; k = k + 1) begin
        sel = k; @(negedge clk); @(negedge clk);
        if ($signed(out) !== want) begin
          errors = errors + 1;
          $display("FAIL cell %0d got %0d want %0d", k, $signed(out), want);
        end
      end
    end
  endtask
  initial begin
    repeat (2) @(negedge clk); rst_n = 1;
    run(4'd1,    4'd1,    4,  4);      // basic
    run(4'b1000, 4'b1000, 20, 1280);   // -8 * -8
    run(4'b1000, 4'd7,    10, -560);   // -8 * 7
    if (errors == 0) $display("TB PASS"); else $display("TB FAIL (%0d errors)", errors);
    $finish;
  end
endmodule
EOF

cat > mac/constraint.sdc <<'EOF'
create_clock -name core_clock -period 2.0 [get_ports clk]
set_input_delay  0.2 -clock core_clock [delete_from_list [all_inputs] [get_ports clk]]
set_output_delay 0.2 -clock core_clock [all_outputs]
EOF

cat > mac/config.mk <<'EOF'
export DESIGN_NAME      = mac_array
export PLATFORM         = nangate45
export VERILOG_FILES    = /work/mac/mac_array.v
export SDC_FILE         = /work/mac/constraint.sdc
export CORE_UTILIZATION = 40
export PLACE_DENSITY    = 0.60
EOF

# ---------------------------------------------------------------- 2. tier 1 (native)
log "2. Tier 1: OSS CAD Suite (native arm64) - testbench + synth-only"
if [ ! -d oss-cad-suite ]; then
  URL=$(curl -s https://api.github.com/repos/YosysHQ/oss-cad-suite-build/releases/latest \
        | grep browser_download_url | grep "$OSS_ASSET" | grep '\.tgz"' | head -1 | cut -d'"' -f4)
  [ -n "$URL" ] || { echo "Could not find OSS CAD Suite $OSS_ASSET asset"; exit 1; }
  curl -L -o oss.tgz "$URL"
  xattr -d com.apple.quarantine oss.tgz 2>/dev/null || true
  tar xzf oss.tgz && rm oss.tgz
fi
# shellcheck disable=SC1091
source oss-cad-suite/environment

t0=$(now)
iverilog -g2005 -o mac/tb.vvp mac/tb.v mac/mac_array.v && vvp mac/tb.vvp | tee mac/tb.log
record "tier1 testbench: $(grep -E 'TB (PASS|FAIL)' mac/tb.log || echo 'no result')  ($(( $(now) - t0 ))s)"

t0=$(now)
yosys -p "read_verilog mac/mac_array.v; synth -top mac_array; abc; stat" > mac/yosys.log 2>&1 || true
record "tier1 yosys synth: $(( $(now) - t0 ))s  cells: $(grep -iE '^ +[0-9]+ +cells$|Number of cells' mac/yosys.log | tail -1 | grep -oE '[0-9]+' | head -1)"

# ---------------------------------------------------------------- 3. pull image
log "3. Pulling ORFS image ($IMAGE, $PLAT)"
t0=$(now)
docker pull --platform "$PLAT" "$IMAGE"
record "image pull: $(( $(now) - t0 ))s (one-time)"

# Runs one ORFS flow in a fresh container; copies reports/logs to $WORK/out/<tag>.
orfs_run() {  # $1 = DESIGN_CONFIG path (in container), $2 = output tag
  docker run --rm --platform "$PLAT" -v "$WORK":/work "$IMAGE" bash -c "
    FLOW=\$(ls -d /OpenROAD-flow-scripts/flow 2>/dev/null || find / -maxdepth 4 -type d -name flow -path '*OpenROAD-flow-scripts*' 2>/dev/null | head -1)
    cd \"\$FLOW\"
    [ -f ../env.sh ] && source ../env.sh >/dev/null 2>&1 || true
    make DESIGN_CONFIG=$1 LEC_CHECK=0 > /work/out/$2.make.log 2>&1; rc=\$?
    mkdir -p /work/out/$2 && cp -r reports logs /work/out/$2/ 2>/dev/null || true
    exit \$rc"
}
slack_of() {  # best-effort: pull slack lines from the finish report
  grep -rhiE 'worst slack|wns|tns' "$WORK/out/$1/reports" 2>/dev/null | head -3 | tr '\n' ' '
}

# ---------------------------------------------------------------- 4. gcd sanity
log "4. ORFS sanity run: built-in gcd example"
t0=$(now)
if orfs_run ./designs/nangate45/gcd/config.mk gcd; then st=OK; else st=FAILED; fi
GCD_S=$(( $(now) - t0 ))
record "gcd full flow: $st  ${GCD_S}s"
[ "$st" = OK ] || { echo "gcd failed - see $WORK/out/gcd.make.log"; exit 1; }

# ---------------------------------------------------------------- 5. MAC single run
log "5. ORFS: 4x4 INT4 MAC array, single run"
t0=$(now)
if orfs_run /work/mac/config.mk mac1; then st=OK; else st=FAILED; fi
MAC_S=$(( $(now) - t0 ))
record "mac full flow: $st  ${MAC_S}s  timing: $(slack_of mac1)"

# ---------------------------------------------------------------- 6. parallel throughput
log "6. ORFS: $PAR MAC runs in parallel"
t0=$(now)
pids=""
for n in $(seq 1 $PAR); do orfs_run /work/mac/config.mk "par$n" & pids="$pids $!"; done
ok=0; for p in $pids; do wait "$p" && ok=$((ok+1)) || true; done
PAR_S=$(( $(now) - t0 ))
record "parallel x$PAR: $ok/$PAR OK  ${PAR_S}s  => ~$(( ok * 3600 / (PAR_S > 0 ? PAR_S : 1) )) full runs/hour"

# ---------------------------------------------------------------- 7. verdict
log "7. Verdict"
if [ "$MAC_S" -le 180 ] && [ "$ok" -eq "$PAR" ]; then
  record "VERDICT: laptop is viable (single MAC run <= 3 min, parallel runs OK)."
else
  record "VERDICT: use an x86 cloud VM (16 cores / 32 GB) for tier 2; keep laptop for tier 1 + dev."
fi
echo; echo "Summary saved to $RESULTS"

#!/bin/bash
# Build one kit design with one nextpnr build and seed, then run regcorr with ALU cut points.
# Usage: check.sh DESIGN SEED NEXTPNR_VENV   (e.g. check.sh crtb_2310 1 .venv-old)
set -e
G=$(cd "$(dirname "$0")/.." && pwd); V=$G/.venv/bin; N=$G/$3/bin; K=$G/ext514/kit
D=$G/ext514/b/$1_$3_s$2; mkdir -p $D; cd $D
[ -f top.json ] || nice timeout 900 yosys -q -l y.log -p "read_verilog $K/$1.v; synth_gowin -top top -family gw2a -nodsp -json top.json"
[ -f pnr.json ] || nice timeout 1800 $N/yowasp-nextpnr-himbaechel-gowin --json top.json --write pnr.json --device GW2AR-LV18QN88C8/I7 --vopt family=GW2A-18C --vopt cst=$K/rad_min_uart.cst --seed $2 -q
nice timeout 900 $V/gowin_pack -d GW2A-18C -o top.fs pnr.json
nice timeout 1800 $V/python $G/unpack_full.py -d GW2A-18C -o unpacked.v top.fs >unpack.log 2>&1
cd $G && nice timeout 3600 python3 regcorr.py $D/top.json $D/pnr.json $D/unpacked.v $D/rcB alus | sed "s/^/$1 $3 seed $2: /"

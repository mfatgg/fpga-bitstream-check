#!/bin/bash
# Build crtb_9699690 for one nextpnr seed and run regcorr with ALU cut points. Usage: sweep.sh SEED
set -e
G=$(cd "$(dirname "$0")/.." && pwd); V=$G/.venv/bin; K=$G/ext514/kit
D=$G/ext514/b/9699690_s$1; mkdir -p $D; cd $D
[ -f top.json ] || nice timeout 900 yosys -q -l y.log -p "read_verilog $K/crtb_9699690.v; synth_gowin -top top -family gw2a -nodsp -json top.json"
[ -f pnr.json ] || nice timeout 1800 $V/yowasp-nextpnr-himbaechel-gowin --json top.json --write pnr.json --device GW2AR-LV18QN88C8/I7 --vopt family=GW2A-18C --vopt cst=$K/rad_min_uart.cst --seed $1 -q
nice timeout 900 $V/gowin_pack -d GW2A-18C -o top.fs pnr.json
nice timeout 1800 $V/python $G/unpack_full.py -d GW2A-18C -o unpacked.v top.fs >unpack.log 2>&1
sha256sum top.fs > top.fs.sha256
cd $G && nice timeout 3600 python3 regcorr.py $D/top.json $D/pnr.json $D/unpacked.v $D/rcB alus | sed "s/^/seed $1: /"

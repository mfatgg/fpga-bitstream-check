"""Negative control for regcorr: change the INIT of one raw-LUT ALU in the cut bitstream netlist.

Usage: python3 mutant.py RC_DIR INSTANCE XORMASK OUT_DIR
Copies RC_DIR/gold.v and writes OUT_DIR/gate.v with `defparam INSTANCE.INIT` XORed by XORMASK.
A used ALU inverted (mask ffff) must make regcorr report an unproven register.
"""

import re
import shutil
import sys
from pathlib import Path


def mutate(text: str, inst: str, mask: int) -> str:
    pat = re.compile(rf"^(defparam {re.escape(inst)}\.INIT = 16'h)([0-9a-fA-F]{{4}});", re.M)
    m = pat.search(text)
    if not m:
        raise SystemExit(f"no defparam for {inst}")
    return text[:m.start()] + f"{m[1]}{int(m[2], 16) ^ mask:04x};" + text[m.end():]


if __name__ == "__main__":
    rc, inst, mask, out = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3], 16), Path(sys.argv[4])
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy(rc / "gold.v", out / "gold.v")
    (out / "gate.v").write_text(mutate((rc / "gate.v").read_text(), inst, mask))
    print("mutated", inst)

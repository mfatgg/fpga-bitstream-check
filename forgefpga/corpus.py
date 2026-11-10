"""corpus: read the fabric of every SLG47910 bitstream file under the fetched sources (see fetch.sh).

Containers are recognised by size: MCU image (46,408 bytes, fabric at byte 1,320, zero padding checked),
FLASH_MEM (fabric + 40), OTP (fabric + 60) and raw fabric (45,056). Each distinct fabric is kept once.
Hand-patched files (a path containing "patched") are skipped: they are not tool output.
"""

from pathlib import Path

FABRIC_BYTES = 1024 * 11 * 4
MCU_SIZE, MCU_FABRIC_OFF, MCU_LEAD_ZEROS = 46_408, 1320, 1280
HEADER_BYTES = {FABRIC_BYTES + 40: 40, FABRIC_BYTES + 60: 60, FABRIC_BYTES: 0}


def fabric_of(data: bytes) -> bytes | None:
    """The 45,056 fabric bytes of a container, or None if the data is not one."""
    if len(data) == MCU_SIZE:
        end = MCU_FABRIC_OFF + FABRIC_BYTES
        if any(data[:MCU_LEAD_ZEROS]) or any(data[end:]):
            return None
        return data[MCU_FABRIC_OFF:end]
    if len(data) in HEADER_BYTES:
        return data[HEADER_BYTES[len(data)]:]
    return None


def source_roots(work: Path) -> list[Path]:
    return [work / "shrike"] + sorted(p for p in work.iterdir() if p.is_dir() and p.name != "shrike")


def load(work: Path) -> list[tuple[str, bytes]]:
    """(path relative to WORK, fabric) for each distinct fabric, first copy wins."""
    out, seen = [], set()
    for root in source_roots(work):
        for path in sorted(root.rglob("*.bin")):
            if ".git" in path.parts or "patched" in str(path.parent):
                continue
            if path.stat().st_size not in (MCU_SIZE, *HEADER_BYTES):
                continue
            fabric = fabric_of(path.read_bytes())
            if fabric is None or fabric in seen:
                continue
            seen.add(fabric)
            out.append((str(path.relative_to(work)), fabric))
    return out

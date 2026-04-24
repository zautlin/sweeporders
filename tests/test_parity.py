"""Byte-diff harness: compare current outputs against tests/parity_baseline/.
Local parity uses CBA/20240505. Multi-day/multi-ticker parity runs on server (Task 9).

NOTE: The pipeline partitions by internal trade dates (2024-09-04, 2024-09-05)
derived from the CBA/20240505 raw input files. The baseline captures both
date-partitioned subdirs produced by `python main.py --ticker cba --date 20240505`.

NOTE on .csv.gz: gzip embeds a mtime in the header, so two identical-content
compressions produce different raw bytes. We decompress before hashing.
"""
from pathlib import Path
import gzip
import hashlib

BASELINE = Path(__file__).parent / "parity_baseline" / "20240505_cba"
LIVE_PROCESSED = Path(__file__).parent.parent / "data" / "processed"
LIVE_OUTPUTS   = Path(__file__).parent.parent / "data" / "outputs"


def _hash(p: Path) -> str:
    raw = p.read_bytes()
    if p.suffix == ".gz":
        raw = gzip.decompress(raw)
    return hashlib.sha256(raw).hexdigest()


def _diff_tree(baseline_root: Path, live_root: Path) -> list[str]:
    diffs = []
    for b in sorted(baseline_root.rglob("*.csv")) + sorted(baseline_root.rglob("*.csv.gz")):
        rel = b.relative_to(baseline_root)
        l = live_root / rel
        if not l.exists():
            diffs.append(f"MISSING in live: {rel}")
        elif _hash(b) != _hash(l):
            diffs.append(f"CONTENT-DIFF: {rel}")
    return diffs


def test_processed_parity():
    diffs = _diff_tree(BASELINE / "processed", LIVE_PROCESSED)
    assert not diffs, "\n".join(diffs)


def test_outputs_parity():
    diffs = _diff_tree(BASELINE / "outputs", LIVE_OUTPUTS)
    assert not diffs, "\n".join(diffs)

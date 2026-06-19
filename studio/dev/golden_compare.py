"""Compare two golden_session dumps for WHOLE-API numerical equivalence (eps 0 / 1e-9).

Walks both JSON trees in lockstep; any structural mismatch or any float differing by more than
EPS is reported (up to a cap), with the max abs diff and the count of compared leaves printed.
Exit 0 iff every leaf matches; non-zero otherwise (so it can gate CI / the commit). This was
the F1 god-object-decomposition equivalence gate (paired with studio.dev.golden_session_dump).
Usage:  python -m studio.dev.golden_compare <golden.json> <candidate.json>
"""
from __future__ import annotations

import json
import sys

EPS = 1e-9


def walk(a, b, path, diffs, stats):
    if isinstance(a, dict) and isinstance(b, dict):
        ka, kb = set(a), set(b)
        if ka != kb:
            diffs.append(f"{path}: key mismatch +{kb - ka} -{ka - kb}")
            return
        for k in sorted(ka):
            walk(a[k], b[k], f"{path}.{k}", diffs, stats)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            diffs.append(f"{path}: list len {len(a)} != {len(b)}")
            return
        for i, (x, y) in enumerate(zip(a, b, strict=True)):  # lengths checked equal above
            walk(x, y, f"{path}[{i}]", diffs, stats)
    elif isinstance(a, bool) or isinstance(b, bool):
        stats["n"] += 1
        if a != b:
            diffs.append(f"{path}: bool {a} != {b}")
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
        stats["n"] += 1
        d = abs(float(a) - float(b))
        if d > stats["max"]:
            stats["max"] = d
            stats["max_path"] = path
        if d > EPS:
            diffs.append(f"{path}: {a} != {b} (|Δ|={d:g})")
    else:
        stats["n"] += 1
        if a != b:
            diffs.append(f"{path}: {a!r} != {b!r}")


def main():
    golden, candidate = sys.argv[1], sys.argv[2]
    a = json.load(open(golden))
    b = json.load(open(candidate))
    diffs: list[str] = []
    stats = {"n": 0, "max": 0.0, "max_path": ""}
    walk(a, b, "root", diffs, stats)
    print(f"compared {stats['n']} leaf values; max |Δ| = {stats['max']:g} "
          f"at {stats['max_path']}")
    if diffs:
        print(f"MISMATCH: {len(diffs)} differing leaves (showing up to 40):")
        for d in diffs[:40]:
            print("  " + d)
        sys.exit(1)
    print("EQUIVALENT: every leaf matches within eps", EPS)


if __name__ == "__main__":
    main()

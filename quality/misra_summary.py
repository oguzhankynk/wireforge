"""Count the findings of a cppcheck XML v2 report per rule id (used by quality/run_misra.sh)."""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

SCOPES = ("runtime/", "tests/generated/", "build/")
ERROR = re.compile(r'<error id="([^"]+)"')
LOCATION = re.compile(r'<location file="([^"]*)"')


def count(text: str) -> Counter[str]:
    """Count findings per rule id, for findings whose first location is in scope."""
    counts: Counter[str] = Counter()
    rule: str | None = None
    for line in text.splitlines():
        found = ERROR.search(line)
        if found:
            rule = found[1]
            continue
        place = LOCATION.search(line)
        if place and rule is not None:
            if any(part in place[1] for part in SCOPES):
                counts[rule] += 1
            rule = None
    return counts


def main(argv: list[str]) -> int:
    """Print the number of findings per rule and in total."""
    counts = count(Path(argv[1]).read_text(encoding="utf-8"))
    for rule, number in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        sys.stdout.write(f"{number:6d}  {rule}\n")
    sys.stdout.write(f"{sum(counts.values()):6d}  total in {len(counts)} rules\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

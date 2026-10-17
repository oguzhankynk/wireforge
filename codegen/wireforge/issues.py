"""Diagnostics shared by all WireForge front-end stages."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Issue:
    """One problem, located by file, line and JSON pointer."""

    file: str
    line: int | None
    pointer: str
    message: str
    severity: str = "error"

    def __str__(self) -> str:
        """Render as `file:line: [warning: ]/pointer: message`."""
        where = f"{self.file}:{self.line}" if self.line is not None else self.file
        prefix = "warning: " if self.severity == "warning" else ""
        return f"{where}: {prefix}{self.pointer or '/'}: {self.message}"


def errors_only(issues: list[Issue]) -> list[Issue]:
    """Drop warnings."""
    return [issue for issue in issues if issue.severity == "error"]

"""Coverage points: what a generated test project has to exercise, and what it does exercise.

The generated code has paths that only a test of a certain kind reaches: the frame writer of every
message with too little room, the receiver check of every message, the serialize call without room,
the decode error of every message. A generator that forgets one for some messages (the first
message is tested, the second is not) leaves generated code that no generated test runs, and the
100 percent coverage gate then fails in the user's project.

`Points` closes that hole at generation time. The writer of a test source first `require`s the
points that follow from the model (computed from the cases of the model, not from the loops that
write the tests) and each test it writes `exercise`s the points it covers. A point that is required
and not exercised is a defect of the generator, reported when the source is rendered.
"""

from __future__ import annotations

Key = tuple[str, ...]
SHOWN = 8


def _key(kind: str, scope: tuple[str | None, ...]) -> Key:
    """Return the key of a point: the kind and its scope without trailing empty parts."""
    parts = [kind, *(part or "" for part in scope)]
    while len(parts) > 1 and not parts[-1]:
        parts.pop()
    return tuple(parts)


class CoveragePointError(Exception):
    """Points the model requires that no generated test exercises (a generator defect)."""


class Points:
    """Required and exercised coverage points of one generated test source."""

    def __init__(self, source: str) -> None:
        """Start the points of the test source called `source` (for the message of an error)."""
        self.source = source
        self.required: set[Key] = set()
        self.exercised: set[Key] = set()

    def require(self, kind: str, *scope: str | None) -> None:
        """Declare that the tests must exercise `kind` for `scope` (profile, owner, detail...)."""
        self.required.add(_key(kind, scope))

    def exercise(self, kind: str, *scope: str | None) -> None:
        """Record that a generated test exercises `kind` for `scope`."""
        self.exercised.add(_key(kind, scope))

    def gaps(self) -> list[Key]:
        """Return the required points no test exercises, in a stable order."""
        return sorted(self.required - self.exercised)

    def check(self) -> None:
        """Raise when a required point is not exercised."""
        missing = self.gaps()
        if missing:
            shown = "; ".join(" / ".join(part for part in key if part) for key in missing[:SHOWN])
            more = f" (and {len(missing) - SHOWN} more)" if len(missing) > SHOWN else ""
            message = (
                f"{self.source}: the tests do not exercise {len(missing)} required point(s): "
                f"{shown}{more}. This is a defect of the generator."
            )
            raise CoveragePointError(message)

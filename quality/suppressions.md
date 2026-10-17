# Suppression registry

Policy target: **0 entries**. Every suppression marker or scoped config override in the repository
must have a row here (file, marker, count, justification, reviewer). `quality/check_suppressions.py`
fails CI when code and registry disagree in either direction.

| ID | File | Marker | Count | Justification | Reviewer |
|----|------|--------|-------|---------------|----------|
| S-001 | tests/support/.clang-tidy | config-override | 1 | Allocation trap replaces global operator new/delete and must call malloc/free; disables owning-memory/no-malloc for that directory only. | project lead |
| S-002 | pyproject.toml | per-file-ignores | 1 | pytest assertions (S101) and literal expected values (PLR2004) in tests/python only. | project lead |

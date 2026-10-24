# Suppression registry

Policy target: **0 entries**. Every suppression marker or scoped config override in the repository
must have a row here (file, marker, count, justification, reviewer). `quality/check_suppressions.py`
fails CI when code and registry disagree in either direction.

| ID | File | Marker | Count | Justification | Reviewer |
|----|------|--------|-------|---------------|----------|
| S-001 | tests/support/.clang-tidy | config-override | 1 | Allocation trap replaces global operator new/delete and must call malloc/free; disables owning-memory/no-malloc for that directory only. | project lead |
| S-002 | pyproject.toml | per-file-ignores | 1 | pytest assertions (S101) and literal expected values (PLR2004) in tests/python only. | project lead |
| S-003 | codegen/wireforge/server/oidc.py | noqa | 1 | `urlopen` fetches the operator-supplied `--oidc-issuer`'s discovery document and JWKS, not request input; there is no caller-controlled scheme to restrict. | project lead |
| S-004 | codegen/wireforge/mavlink_xml.py | noqa | 1 | `ElementTree.fromstring` (S314) reads a MAVLink dialect only after a document type or entity declaration was refused, so no entity can be expanded; the importer is offered on the command line and in Python, not by the server. | project lead |

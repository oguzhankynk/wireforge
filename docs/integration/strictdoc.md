# StrictDoc

Verified against: not yet (pilot pending).

StrictDoc keeps requirements as text in git, like WireForge. To see the low-level requirements in it, convert
`requirements.csv` (docs/integration/CSV.md) into a StrictDoc document with a short script of your own, with one
requirement per row, the `id` as UID and `parents` as parent links. Run StrictDoc's own checks on the result. WireForge
does not need StrictDoc: its documents already contain the traceability matrix.

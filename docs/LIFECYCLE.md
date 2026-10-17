# Requirements, design and test documents

`wireforge docs <project> -o <dir>` writes the documents a software lifecycle needs from the model: requirements,
design, test specification, traceability and the checklists around them. The same text comes from
`GET /api/projects/{id}/docs` (a zip) and the MCP tool `lifecycle_docs`. The output depends only on the model: no
date, user name or absolute path is written, so the same model gives the same bytes and the files diff cleanly.

## What is written
| File | Content |
|------|---------|
| `index.md` | baseline (model hash, tool version), list of documents, counts, result of the strict rules, limits |
| `requirements.md`, `.csv` | high-level requirements, low-level requirements derived from the model, derived requirements |
| `design.md` | architecture overview, generated files, interfaces (header, trailer, messages, buffers per profile), messages and types, status codes and the requirements behind them, resources |
| `interfaces_coupling.md` | data coupling table per interface, control coupling, assumptions |
| `test_spec.md`, `.csv` | every generated test: executable, profile, kind, oracle, requirements verified, procedure, expected result |
| `test_report.md` | test counts; results are not part of this run ("Test results: not provided.") |
| `traceability.md`, `.csv` | requirement to model element to LLR to code to test, and tests back to requirements, with the gaps |
| `environment.md` | model files with sha256, WireForge, Python and library versions |
| `code_standard.md` | rules the generated code follows and the evidence for each |
| `review_checklists.md` | checklists for requirements, design, code and tests; items WireForge can decide are answered (`[auto: PASS]`), the rest are open boxes for the reviewer |
| `do178c_dal_c.md` | which document is evidence for which DO-178C objective at DAL C; objectives the applicant owns are marked |

The directory is created when missing. Only files with these names are written; nothing else in it is touched.

## Who writes what
The high-level requirements are yours: write them in the `requirements` section and point model elements at them with
`reqs` (docs/SCHEMA_SPEC.md section 17). WireForge derives the low-level requirements, links them to the generated
tests and writes everything down. A low-level requirement without a high-level one is *derived*; derived requirements
are listed so they can go to the system safety assessment. They are not an error.

## `--strict`
Without `--strict` the command only fails when the project is invalid. With it, the exit code is 1 when a reviewer would
reject the model; each finding is printed and listed in `index.md`:

| Rule | Finding |
|------|---------|
| DS-1 | the project defines no requirements |
| DS-2 | a message or interface without any high-level requirement behind it |
| DS-3 | a low-level requirement without a generated test (requirements the runtime tests verify are exempt) |
| DS-4 | a requirement verified by `test` that no model element uses |

With no requirements only DS-1 is reported. The documents are written in any case.

## What this is not
These documents are evidence for your own process. WireForge does not claim that any output is certified or compliant,
and the code generator is not qualified: the generated code is verified like hand-written code, and the documents show
that verification (requirement, test, result). The correctness of the high-level requirements, tests on the target and
the process around them (planning, configuration management, quality assurance, certification liaison) remain yours.
The objective table in `do178c_dal_c.md` uses our own wording and the objective numbers; confirm the rows marked
`check` against your licensed copy of DO-178C.

`quality/run_evidence.sh` writes the documents of every example to `build/evidence/docs_<project>/`.

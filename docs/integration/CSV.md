# CSV files

The column names are a contract; a change is a breaking change. Files are UTF-8 with Unix line ends, values with commas or
quotes are quoted, a list in one cell is separated by spaces.

Verified against: not yet (pilot pending).

## requirements.csv
| Column | Content |
|--------|---------|
| `id` | requirement id (`HLR-...`, or `LLR-...` / your numbering) |
| `level` | `HLR` or `LLR` |
| `kind` | verification (`test`, `analysis`, `review`) for `HLR`; the kind of requirement for `LLR` |
| `text` | requirement text |
| `elements` | model elements that implement it (JSON pointers) |
| `parents` | for `LLR`: the high-level ids behind it; empty means derived |

## traceability.csv
| Column | Content |
|--------|---------|
| `llr` | low-level requirement id |
| `kind` | kind of requirement |
| `parents` | high-level ids |
| `code` | generated file and line |
| `tests` | tests that verify it (`Suite.Name`) |
| `derived` | `yes` when no high-level requirement is behind it |
| `result` | `passed`, `failed`, `skipped`, `not run`, empty without results |

## test_spec.csv
`id`, `executable`, `profile`, `kind` (`normal` or `robustness`), `oracle` (`reference-codec` or `hlr-vector`),
`verifies` (requirement ids), `procedure`, `expected`.

## Importing into a tool
Map `id` to the identifier, `text` to the description and `parents` to the link to the high-level requirement. The ReqIF
file `llr.reqifz` carries the same information with the links as relations.

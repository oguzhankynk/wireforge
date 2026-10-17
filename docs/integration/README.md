# Integration with your requirements and test tools

WireForge exchanges files with your tools and keeps everything in git. There is no live integration.

## Who owns what
| Data | Owner | In WireForge |
|------|-------|--------------|
| High-level requirements | your requirements tool, or the `requirements` section if you have none | a snapshot of one baseline (`wireforge reqs-import`), never edited by hand |
| Low-level requirements and design | the WireForge model (git) | exported read-only as ReqIF or CSV |
| Code and tests | git (generated) | requirement ids in comments and test properties, permalinks |
| Test results and coverage | the CI artifacts | JUnit and Cobertura files; the documents list their paths |
| Traceability matrix | derived, never kept by hand | `traceability.md` and `.csv`; your tool can scan the repository instead |

The link between the worlds is the stable id: the id of a high-level requirement is the one in your tool, the id of a
low-level requirement comes from `llr_ids.lock`, and both are written into code, tests and documents as text
(docs/integration/TAGS.md).

## Set-up by tool
| What you have | High-level requirements | Low-level requirements, code, tests | Results |
|---------------|-------------------------|-------------------------------------|---------|
| DOORS, DOORS Next | module exported as ReqIF, `reqs-import` | import `llr.reqifz` (match by `ReqIF.ForeignID`), permalinks | JUnit, `traceability.csv` |
| Polarion, codebeamer, Jama | the tool's ReqIF or CSV export | ReqIF or CSV import (docs/integration/CSV.md) | JUnit and Cobertura |
| Reqtify, LDRA TBmanager | the source in the tool | the tool scans the repository (docs/integration/TAGS.md) | JUnit |
| none | the `requirements` section (git) | `traceability.md`; StrictDoc if you want a tool | `test_report.md` |

Each tool page says what to try first. They are written from the formats and have not been checked against a licensed
copy of the tools yet.

## Pages
`TAGS.md` (the tag convention), `CSV.md` (columns of the CSV files), `doors_next.md`, `polarion.md`, `codebeamer.md`,
`jama.md`, `reqtify.md`, `strictdoc.md`, `word_templates.md`, `rule_checkers.md` (reports of your coding-rule checker).

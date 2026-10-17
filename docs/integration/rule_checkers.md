# Coding-rule checkers

`wireforge docs --rule-findings <file>` reads the report of your own checker. Two formats are understood: SARIF 2.1.0
(`version` must be `"2.1.0"`) and cppcheck XML version 2. The tool name is taken from `runs[].tool.driver.name`
(SARIF) or is `cppcheck`; `deviations.yaml` refers to it with the same name, case-insensitive. The rule is `ruleId` or the
`id` attribute, written as the tool writes it.

Verified against: not yet (pilot pending).

## Exporting
| Tool | How to get a report |
|------|---------------------|
| cppcheck | `cppcheck --xml --xml-version=2 --language=c++ --std=c++17 -I <runtime>/include <generated dir> 2> findings.xml` |
| Any SARIF writer | Export "SARIF" from its GUI or CLI; one file per run, or several `--rule-findings` options. |

Run the checker on the generated headers and on the runtime. Findings in other files are counted and ignored, so the
report can come from a whole build.

## Justifying a finding
```yaml
schema_version: 1
deviations:
  - kind: rule
    tool: Checker A
    rule: M5-0-1
    file: "avionics_demo/interfaces/*.hpp"
    function: "*decode*"      # optional
    category: justified       # justified | false-positive | not-applicable
    rationale: Why the code is safe here.
    reviewer: J. Doe
```
The file glob is matched against the project-relative or runtime-relative header name that the document shows.

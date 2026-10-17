# Tag convention

Tools that scan files for requirement ids find them in these places. The convention is part of the interface:
a change is a breaking change of WireForge.

Verified against: not yet (pilot pending).

## In generated C++
```
/// Requirements: HLR-SNS-001, HLR-SNS-004; LLR-SensorReading.LAYOUT, LLR-SensorReading.temperature.RANGE
```
High-level ids come first, then a semicolon, then the low-level ids (shown with your numbering when a lock file exists).
The line wraps with `///` at 100 columns. Regular expression:

```
Requirements:\s*([A-Za-z0-9_.:-]+(?:\s*[,;]\s*[A-Za-z0-9_.:-]+)*)
```
The comment appears on structs, enums, checksums and interfaces, and only in projects that define a `requirements`
section.

## In generated tests
Every test starts with
```cpp
::testing::Test::RecordProperty("requirements", "LLR-SensorReading.LAYOUT,HLR-SNS-001");
```
GoogleTest writes it into its XML result as `<property name="requirements" value="..."/>`. The ids of requirement vectors
are high-level ids; the others are low-level ids.

## In Markdown
Each requirement, test and element has an anchor and shows the id in the same line:
`<a id="llr-sensorreading-layout"></a>LLR-SensorReading.LAYOUT`. The anchor is the id in lower case with every
character outside `a-z0-9-` replaced by `-`.

## Characters
An id uses `A-Z a-z 0-9 _ . : -`. The `format` of `llr_ids.lock` cannot produce anything else.

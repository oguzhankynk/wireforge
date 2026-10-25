# 11. Documents and evidence

**What we will do:** turn the model, the generated tests and their results into requirements, design, test and
traceability documents. This is for teams that work with a development or assurance process; nothing in chapters 1 to
10 needs it.

## Requirements in the model
The MAVLink 2 model already has high-level requirements: `requirements:` in `drone/project.yaml` (`MAV2-001` to
`MAV2-005`), and elements point at them with `reqs:`. WireForge derives the low-level requirements from the model
itself (the layout of every message, the range of every field, the trimming of the interface, ...) and the generated
tests record which ones they verify.

## Documents
<!-- tutorial: run -->
```sh
wireforge test drone -o build/drone-evidence --coverage
wireforge docs drone -o build/docs --results build/drone-evidence/drone/generated_tests/build/coverage
ls build/docs
```
`build/docs` holds Markdown documents (and CSV): the requirements, the design of every message and interface, the test
procedures and their results, and a traceability matrix from each high-level requirement through the low-level
requirements and the generated code to the tests that verify it, with their result and the coverage. `--export reqif`
writes ReqIF for requirement tools such as DOORS Next or Polarion.

<!-- tutorial: run -->
```sh
wireforge trace drone --format csv -o build/trace.csv
```

## What this is and is not
The documents support a process such as DO-178C, ISO 26262 or IEC 61508 and the assessment of WireForge as a tool in
it. They do not by themselves give conformity, certification or tool qualification; those depend on your whole project
and its assessor.

**More:** [lifecycle documents](../LIFECYCLE.md), [DO-178C](../DO178C.md), [tool integrations](../integration/README.md).

That is the end of the tutorial. The [user guide](../GUIDE.md) and the reference documents go on from here.

## What and why
<!-- What changes, and the problem it solves. -->

## Checklist
- [ ] Tests for the change (unit, generated test project or Python), and `quality/run_all.sh` passes locally
      (or the gates the change touches: see CONTRIBUTING.md)
- [ ] Documentation updated (docs/, the tutorial when a step changes)
- [ ] Design rationale in docs/DECISIONS.md when the change takes a design decision
- [ ] Generated-code snapshots reviewed if they changed (`WIREFORGE_UPDATE_SNAPSHOTS=1`)
- [ ] No bytes added to any wire format; no heap, exceptions or RTTI in the runtime or generated code

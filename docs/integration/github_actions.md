# GitHub Actions

The action `action.yml` in the root of this repository runs `wireforge ci` on a project: it validates it, compares it
with the base of the pull request (wire compatibility and the version bump each changed interface needs), builds and
runs the generated tests, and shows one summary on the run. The same command runs anywhere (`wireforge ci --help`), so
GitLab and a developer's machine give the same answer (docs/integration/gitlab_ci.yml).

```yaml
name: wireforge
on: pull_request
jobs:
  check:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }          # the base revision must be readable
      - uses: oguzhankynk/wireforge@v0.1.0
        with:
          project: icd                    # directory with project.yaml
          # test: "true"                  # generate, build and run the tests
          # plugins: icd/plugins/sum.py   # custom checksum plugins
          # comment: "true"               # also comment on the pull request
```

## What the summary says
| Check | Meaning |
|-------|---------|
| Validation | the structural and semantic rules of docs/SCHEMA_SPEC.md |
| Wire compatibility | per interface: the version bump the change needs (none, patch, minor, major) against the bump made; red when the version is too low |
| Tests | the result line of the generated tests (and the coverage gate of the generated code) |

The changes of each interface are listed (the first 20), so a reviewer sees what moved on the wire. A project that did
not exist at the base is not compared; the summary says so.

## Notes
- **Reproducible:** without `wireforge-version` the action installs the WireForge it is part of, the ref you wrote after
  `uses:`. Pin that ref to a release tag or a commit.
- **Permissions:** the check needs no secret. `comment: "true"` posts the summary as a comment and needs
  `permissions: { pull-requests: write }` for the job.
- **Build tools:** the tests need CMake and a C++17 compiler, which the GitHub-hosted runners have. GoogleTest is
  downloaded by CMake the first time.
- **License:** running the action is free, in commercial product repositories too. Validating a project, comparing it
  with earlier revisions and generating, building and running its tests in CI count as non-production use (LICENSE;
  the generated code and the `runtime/` library are MIT, runtime/LICENSE).
- **Data:** the action collects and sends nothing. Only with `comment: "true"` it posts the summary on the same pull
  request, with the `GITHUB_TOKEN` of your workflow.
- **Support:** open an issue at https://github.com/oguzhankynk/wireforge/issues.
- **Exit code:** the step fails when the validation, the compatibility or the tests fail, after the summary is written.

Verified against: not yet (pilot pending); the action is exercised by this repository's own checks only.

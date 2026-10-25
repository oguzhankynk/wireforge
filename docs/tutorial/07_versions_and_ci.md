# 7. Versions and CI

**What we will do:** let WireForge tell a compatible change from a breaking one, and check every pull request.

## Compare two versions
Keep a copy of the model as it is, then change a description:
<!-- tutorial: run -->
```sh
cp -r drone drone-1.0
wireforge set drone /messages/VfrHud/description "Speeds, altitude and heading for a head-up display (VFR_HUD)."
wireforge compat drone-1.0 drone || echo "the version must change"
```
`compat` lists the change (`doc`: documentation only) and fails: every change of an interface needs a new version, here
a patch version. Give it one:
<!-- tutorial: run -->
```sh
wireforge set drone /interfaces/link/version 1.0.1
wireforge compat drone-1.0 drone
```
Now a change on the wire:
<!-- tutorial: run -->
```sh
wireforge set drone /messages/VfrHud/fields/5/type u8
wireforge compat drone-1.0 drone || echo "breaking, as expected"
```
`throttle` is now one byte instead of two: every receiver of version 1.0 would read VFR_HUD wrongly. `compat` calls it a
breaking change that needs a new major version (2.0.0). Adding a message would be a compatible change (a minor
version), because receivers that do not know it report it as an unknown id and go on. Undo the breaking change:
<!-- tutorial: run -->
```sh
wireforge set drone /messages/VfrHud/fields/5/type u16
wireforge compat drone-1.0 drone
```

## In git
`wireforge ci` does the same against a git revision, validates, and can build and run the generated tests (`--test`):
<!-- tutorial: run -->
```sh
git init -q && git add drone && git -c user.name=me -c user.email=me@example.com commit -qm "drone 1.0.1"
wireforge set drone /messages/Heartbeat/description "Who is there and in which state (HEARTBEAT, 1 Hz)."
wireforge set drone /interfaces/link/version 1.0.2
wireforge ci drone --base HEAD
```
It prints the summary a pull request would get, in Markdown.

## On every pull request
The WireForge repository is also a GitHub Action. In `.github/workflows/wireforge.yml`:
```yaml
name: wireforge
on: pull_request
jobs:
  check:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }          # the base revision must be readable
      - uses: oguzhankynk/wireforge@v0.4.1
        with:
          project: drone
          test: "true"                    # generate, build and run the tests
```
The run shows one summary: validation, the version bump every changed interface needs and the result of the tests.
Running it in CI is free, also in commercial repositories. For GitLab there is an example job in the repository.

**More:** [GitHub Actions](../integration/github_actions.md), [compatibility rules](../SCHEMA_SPEC.md#11-versioning-and-file-identity).

Next: [8. Wireshark](08_wireshark.md)

# Stored regressions

- `<project>/<profile>/*.txt` (`default` for projects without profiles): differential inputs, one request per line
  (`M <message> <hex>` or `F <interface> <hex>`, `#` starts a comment). `python -m wireforge.differential` replays
  them before its random inputs and appends every new mismatch to `found.txt`; commit that file with the fix.
- `fuzz/<target>/`: libFuzzer inputs (crashes and interesting cases). `quality/run_fuzz.sh` uses them as the seed
  corpus of `<target>`; copy a crash file there when fixing it.

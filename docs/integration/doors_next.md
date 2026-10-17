# DOORS Next

Verified against: not yet (pilot pending). This page follows the file formats and the usual import features of the tool;
check the names of the dialogs against the documentation of your version.

## High-level requirements into WireForge
1. Export the module or project with its baseline as ReqIF (or CSV).
2. Run `wireforge reqs-import <project> <file> --baseline <name>`. The identifier is read from `ReqIF.ForeignID` and the
   text from `ReqIF.Text`; if your export uses other attribute names the command lists the ones it found and you name
   them with `--id-attr` and `--text-attr`. For CSV use `--csv-id-column` and `--csv-text-column`.
3. Commit `requirements/hlr_snapshot.yaml`. It records the tool, the baseline and the hash of the file.

## Low-level requirements into DOORS Next
1. Run `wireforge docs <project> -o <dir> --export reqif,csv`.
2. Import `llr.reqifz` (ReqIF). Each low-level requirement carries `ReqIF.ForeignID` (its id), `ReqIF.Text`, `WF.Kind`,
   `WF.Element`, `WF.Source` and `WF.Derived`. Placeholders of type `WF_HLR_REF` carry the id of the high-level
   requirement, and relations of type `WF_REFINES` link each low-level requirement to its placeholders. Match the
   placeholders to your own requirements by id, so the links end at the real requirements.
3. Import the numbering again after a change: ids that did not change keep their identity.

## Code, tests and results
Use the permalinks (`--source-url`) and the tag convention (docs/integration/TAGS.md) to link code and tests; attach
the JUnit files of the run as test results.

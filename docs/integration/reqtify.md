# Reqtify and LDRA TBmanager

Verified against: not yet (pilot pending).

These tools scan sources for ids instead of importing a file.

1. Take the high-level requirements from the source you already have in the tool.
2. Point the tool at the repository (generated code, tests and the Markdown documents).
3. Use the regular expression of docs/integration/TAGS.md to find ids in the `Requirements:` comments, and the Markdown
   anchors for the documents; the test properties are in the JUnit XML.
4. Ids that cannot be matched to a requirement of your tool show up as untraced in the tool's own report.

# Word documents

The Markdown files are the source. For a Word document run pandoc with your company's template:

```sh
pandoc design.md -o design.docx --reference-doc=company.docx
```
Pandoc is not a dependency of WireForge; the tables and anchors survive the conversion. Keep the Markdown in git and
generate the Word files for review or delivery.

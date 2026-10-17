/** The part of the generated header that defines `name` (its struct and functions). */
export function structSource(header: string, name: string): string {
  const start = header.indexOf(`struct ${name} {`);
  if (start < 0) return "";
  const comment = header.lastIndexOf("/// @brief", start);
  const from = comment >= 0 && header.slice(comment, start).split("\n").length <= 2 ? comment : start;
  const next = header.indexOf("\nstruct ", start + 1);
  return header.slice(from, next < 0 ? undefined : next).trimEnd();
}

/** Generated C++ of the selected struct. */
export function CppPreview({ source }: { source: string }) {
  return (
    <section aria-label="C++ preview">
      <h3>C++ preview</h3>
      <pre>{source === "" ? "(save to see the generated code)" : source}</pre>
    </section>
  );
}

import type { Issue } from "../api";

/** Validation issues of the project, or a confirmation that there are none. */
export function IssuesPanel({ issues, local }: { issues: readonly Issue[]; local: readonly string[] }) {
  if (issues.length === 0 && local.length === 0) {
    return <p role="status">No issues.</p>;
  }
  return (
    <ul aria-label="Issues">
      {local.map((problem) => (
        <li key={`local-${problem}`}>{problem}</li>
      ))}
      {issues.map((issue) => (
        <li key={`${issue.file}:${String(issue.line)}:${issue.pointer}:${issue.message}`}>
          {issue.pointer || "/"}: {issue.message}
        </li>
      ))}
    </ul>
  );
}

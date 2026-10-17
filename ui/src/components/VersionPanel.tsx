import { useEffect, useState } from "react";
import type { Api } from "../api";

interface Commit {
  commit: string;
  short: string;
  date: string;
  subject: string;
}

/** Version history of the project (git) and its compatibility with an older revision. */
export function VersionPanel({ api, project, etag }: { api: Api; project: string; etag: string }) {
  const [commits, setCommits] = useState<Commit[]>([]);
  const [report, setReport] = useState("");

  useEffect(() => {
    let active = true;
    void api.history(project).then((result) => {
      if (active) setCommits((result.data as { commits?: Commit[] }).commits ?? []);
    });
    return () => {
      active = false;
    };
  }, [api, project, etag]);

  const compare = async (commit: Commit) => {
    const result = await api.compatWithRevision(project, commit.commit);
    setReport(`${result.ok ? "compatible" : "NOT compatible"} with ${commit.short}\n${result.text}`);
  };

  return (
    <section aria-label="Versions">
      <h2>Versions</h2>
      {commits.length === 0 ? (
        <p>No history (the project is not in a git repository).</p>
      ) : (
        <ul>
          {commits.map((commit) => (
            <li key={commit.commit}>
              {commit.short} {commit.subject}{" "}
              <button type="button" aria-label={`Compare with ${commit.short}`} onClick={() => void compare(commit)}>
                Compare
              </button>
            </li>
          ))}
        </ul>
      )}
      {report !== "" && <pre aria-label="Compatibility report">{report}</pre>}
    </section>
  );
}

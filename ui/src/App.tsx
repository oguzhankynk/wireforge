import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Api, Issue, ProjectSummary, ServiceResult } from "./api";
import { ChecksumEditor } from "./components/ChecksumEditor";
import { EnumEditor } from "./components/EnumEditor";
import { GeneratePanel } from "./components/GeneratePanel";
import { CppPreview, structSource } from "./components/CppPreview";
import { InterfaceEditor } from "./components/InterfaceEditor";
import { NewProject } from "./components/NewProject";
import { NextSteps } from "./components/NextSteps";
import { IssuesPanel } from "./components/IssuesPanel";
import { LayoutDiagram } from "./components/LayoutDiagram";
import { Playground } from "./components/Playground";
import { ProfileEditor } from "./components/ProfileEditor";
import { ProjectSettings } from "./components/ProjectSettings";
import { StructEditor } from "./components/StructEditor";
import { ThemeToggle } from "./components/ThemeToggle";
import { VersionPanel } from "./components/VersionPanel";
import { BUILTIN_TYPES } from "./model";

type Section = "enums" | "types" | "messages" | "checksums" | "interfaces" | "profiles" | "settings";
type Tool = "preview" | "layout" | "playground" | "versions" | "generate";

interface Selection {
  section: Section;
  /** null: a new entry. */
  name: string | null;
}

type Sections = Partial<Record<string, string[]>>;

const SECTION_TITLES: readonly [Section, string, string][] = [
  ["enums", "Enums", "enum"],
  ["types", "Types", "type"],
  ["messages", "Messages", "message"],
  ["interfaces", "Interfaces", "interface"],
  ["profiles", "Profiles", "profile"],
  ["checksums", "Checksums", "checksum"],
];
const TOOLS: readonly [Tool, string][] = [
  ["preview", "C++"],
  ["layout", "Layout"],
  ["playground", "Playground"],
  ["versions", "Versions"],
  ["generate", "Generate"],
];

/** The editor application: project, entry tree, editors, issues and tools. */
export function App({ api }: { api: Api }) {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [project, setProject] = useState("");
  const [sections, setSections] = useState<Sections>({});
  const [issues, setIssues] = useState<Issue[]>([]);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [header, setHeader] = useState("");
  const [etag, setEtag] = useState("");
  const [tool, setTool] = useState<Tool>("preview");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [creating, setCreating] = useState(false);
  // The project just created: its next steps are shown, and its first message opens once loaded.
  const [started, setStarted] = useState("");
  const openFirstOf = useRef("");

  // Loads the project list; keeps the selected project when it still exists. An empty workspace
  // opens the new project form.
  const loadProjects = useCallback(async () => {
    try {
      const list = await api.projects();
      setProjects(list);
      if (list.length === 0) setCreating(true);
      setProject((current) =>
        list.some((item) => item.id === current) ? current : (list[0]?.id ?? ""),
      );
    } catch (failure) {
      setError(String(failure));
    }
  }, [api]);

  useEffect(() => {
    void loadProjects();
  }, [loadProjects]);

  const refresh = useCallback(async () => {
    if (project === "") return;
    const result = await api.project(project);
    const loaded = (result.sections as Sections | undefined) ?? {};
    setSections(loaded);
    if (openFirstOf.current === project) {
      openFirstOf.current = "";
      const first = loaded.messages?.[0];
      if (first !== undefined) setSelection({ section: "messages", name: first });
    }
    setIssues(result.issues);
    setEtag(result.etag ?? "");
    const name = projects.find((item) => item.id === project)?.name ?? "";
    const preview = await api.preview(project);
    const files = (preview.data as { files?: Record<string, string> }).files ?? {};
    setHeader(files[`${name}/model.hpp`] ?? "");
  }, [api, project, projects]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const typeOptions = useMemo(
    () => [...BUILTIN_TYPES, ...(sections.enums ?? []), ...(sections.types ?? [])],
    [sections],
  );
  const checksumOptions = sections.checksums ?? [];

  // A save that finishes after the user moved on must not change the selection.
  const savedIn = (section: Section) => (name: string, result: ServiceResult) => {
    setIssues(result.issues);
    setNotice(`Saved ${section}/${name}.`);
    setSelection((current) =>
      current?.section === section && (current.name === null || current.name === name)
        ? { section, name }
        : current,
    );
    void refresh();
  };

  const editor = () => {
    if (selection === null) return null;
    const key = `${selection.section}/${selection.name ?? ""}`;
    switch (selection.section) {
      case "types":
      case "messages":
        return (
          <StructEditor
            key={key}
            api={api}
            project={project}
            section={selection.section}
            name={selection.name}
            typeOptions={typeOptions}
            checksumOptions={checksumOptions}
            onSaved={savedIn(selection.section)}
          />
        );
      case "interfaces":
        return (
          <InterfaceEditor
            key={key}
            api={api}
            project={project}
            name={selection.name}
            messageOptions={sections.messages ?? []}
            typeOptions={typeOptions}
            checksumOptions={checksumOptions}
            onSaved={savedIn("interfaces")}
          />
        );
      case "enums":
        return <EnumEditor key={key} api={api} project={project} name={selection.name} onSaved={savedIn("enums")} />;
      case "settings":
        return (
          <ProjectSettings
            key={key}
            api={api}
            project={project}
            onSaved={(result) => {
              setIssues(result.issues);
              setNotice("Saved project settings.");
              void loadProjects();
            }}
          />
        );
      case "profiles":
        return (
          <ProfileEditor key={key} api={api} project={project} name={selection.name} onSaved={savedIn("profiles")} />
        );
      case "checksums":
        return (
          <ChecksumEditor
            key={key}
            api={api}
            project={project}
            onSaved={(result) => {
              setIssues(result.issues);
              setNotice("Saved checksum.");
              void refresh();
            }}
          />
        );
    }
  };

  const structSelected =
    selection?.name != null && (selection.section === "types" || selection.section === "messages");

  return (
    <main>
      <header>
        <h1>
          <span className="logo" aria-hidden="true" />
          WireForge
        </h1>
        <label>
          Project
          <select
            value={project}
            onChange={(event) => {
              setProject(event.target.value);
              setSelection(null);
              setStarted("");
              setNotice("");
            }}
          >
            {projects.map((item) => (
              <option key={item.id} value={item.id}>
                {item.id}
              </option>
            ))}
          </select>
        </label>
        <button type="button" onClick={() => { setCreating(true); }}>
          New project
        </button>
        <button type="button" onClick={() => { setSelection({ section: "settings", name: null }); }}>
          Project settings
        </button>
        {error !== "" && <p role="alert">{error}</p>}
        <ThemeToggle />
      </header>
      <nav aria-label="Entries">
        {SECTION_TITLES.map(([section, title, singular]) => (
          <section key={section} aria-label={title}>
            <h2>{title}</h2>
            <ul>
              {(sections[section] ?? []).map((name) => (
                <li key={name}>
                  <button
                    type="button"
                    aria-current={selection?.section === section && selection.name === name ? "true" : undefined}
                    onClick={() => { setSelection({ section, name }); }}
                  >
                    {name}
                  </button>
                </li>
              ))}
            </ul>
            <button type="button" onClick={() => { setSelection({ section, name: null }); }}>
              New {singular}
            </button>
          </section>
        ))}
      </nav>
      <div className="workspace">
        {creating ? (
          <NewProject
            api={api}
            onCancel={() => { setCreating(false); }}
            onCreated={(id) => {
              setCreating(false);
              setSelection(null);
              setNotice(`Created ${id}.`);
              openFirstOf.current = id;
              setStarted(id);
              setProject(id);
              void loadProjects();
            }}
          />
        ) : (
          editor()
        )}
        {started !== "" && started === project && !creating && (
          <NextSteps project={project} onTool={setTool} onClose={() => { setStarted(""); }} />
        )}
        <output aria-label="Save result">{notice}</output>
        <IssuesPanel issues={issues} local={[]} />
        <div role="tablist" aria-label="Tools">
          {TOOLS.map(([value, title]) => (
            <button
              key={value}
              type="button"
              role="tab"
              aria-selected={tool === value}
              onClick={() => { setTool(value); }}
            >
              {title}
            </button>
          ))}
        </div>
        {tool === "preview" && structSelected && selection.name !== null && (
          <CppPreview source={structSource(header, selection.name)} />
        )}
        {tool === "layout" && project !== "" && <LayoutDiagram api={api} project={project} etag={etag} />}
        {tool === "playground" && project !== "" && (
          <Playground
            api={api}
            project={project}
            messages={sections.messages ?? []}
            interfaces={sections.interfaces ?? []}
            profiles={sections.profiles ?? []}
          />
        )}
        {tool === "versions" && project !== "" && <VersionPanel api={api} project={project} etag={etag} />}
        {tool === "generate" && project !== "" && <GeneratePanel api={api} project={project} etag={etag} />}
      </div>
    </main>
  );
}

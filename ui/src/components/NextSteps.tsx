/** What to do after a project was created: edit, try, generate. */
export function NextSteps(props: {
  project: string;
  onTool: (tool: "playground" | "generate") => void;
  onClose: () => void;
}) {
  const { project, onTool, onClose } = props;
  return (
    <section aria-label="Next steps">
      <h2>Next steps</h2>
      <ol>
        <li>Change the fields of the open message to match your protocol and save; problems are listed below.</li>
        <li>
          Encode a value to a frame and decode it again:{" "}
          <button type="button" onClick={() => { onTool("playground"); }}>
            Open Playground
          </button>
        </li>
        <li>
          See the generated code and download it with its tests:{" "}
          <button type="button" onClick={() => { onTool("generate"); }}>
            Open Generate
          </button>
        </li>
      </ol>
      <button type="button" aria-label={`Hide next steps of ${project}`} onClick={onClose}>
        Hide
      </button>
    </section>
  );
}

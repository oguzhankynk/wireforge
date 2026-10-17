// Starts a real WireForge server on a free port over a temporary workspace for the UI tests.
import { execFileSync, spawn, type ChildProcess } from "node:child_process";
import { cpSync, existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { GlobalSetupContext } from "vitest/node";

declare module "vitest" {
  export interface ProvidedContext {
    serverUrl: string;
    workspace: string;
  }
}

const repo = dirname(dirname(dirname(fileURLToPath(import.meta.url))));

async function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      server.close(() => {
        if (address !== null && typeof address === "object") resolve(address.port);
        else reject(new Error("no port"));
      });
    });
  });
}

async function waitFor(url: string): Promise<void> {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try {
      if ((await fetch(url)).ok) return;
    } catch {
      // not up yet
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`server at ${url} did not start`);
}

let server: ChildProcess | undefined;
let workspace = "";

export default async function setup({ provide }: GlobalSetupContext): Promise<() => void> {
  workspace = mkdtempSync(join(tmpdir(), "wireforge-ui-"));
  mkdirSync(join(workspace, "demo"));
  writeFileSync(
    join(workspace, "demo", "project.yaml"),
    "schema_version: 1\nproject: demo\nversion: \"0.1.0\"\nnamespace: demo::link\n",
  );
  // The three-interface example, committed to git so that the version history has a revision.
  cpSync(join(repo, "examples", "02_multi_interface_avionics"), join(workspace, "avionics"), { recursive: true });
  const git = (...args: string[]) =>
    execFileSync("git", ["-C", workspace, "-c", "user.name=ui", "-c", "user.email=ui@test", ...args]);
  git("init", "-q");
  git("add", "-A");
  git("commit", "-qm", "baseline");
  const port = await freePort();
  const venv = join(repo, ".venv", "bin", "python");
  const python = process.env.WIREFORGE_PYTHON ?? (existsSync(venv) ? venv : "python3");
  server = spawn(
    python,
    ["-m", "wireforge.cli", "serve", "--workspace", workspace, "--port", String(port)],
    { env: { ...process.env, PYTHONPATH: join(repo, "codegen") }, stdio: "ignore" },
  );
  const url = `http://127.0.0.1:${String(port)}`;
  await waitFor(`${url}/api/health`);
  provide("serverUrl", url);
  provide("workspace", workspace);
  return () => {
    server?.kill();
    rmSync(workspace, { recursive: true, force: true });
  };
}

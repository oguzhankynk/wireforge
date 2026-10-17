import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// `npm run dev` proxies the API to `wireforge serve` on port 8000.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
  test: {
    environment: "jsdom",
    globalSetup: ["tests/server.setup.ts"],
    // The test files share one server and workspace.
    fileParallelism: false,
    testTimeout: 30000,
    hookTimeout: 60000,
  },
});

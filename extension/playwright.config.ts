import {defineConfig} from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  workers: 1,
  use: {trace: "retain-on-failure"},
  webServer: {
    command: "npx http-server e2e/fixtures -p 8787 -c-1",
    url: "http://127.0.0.1:8787/publisher.html",
    reuseExistingServer: true,
  },
});

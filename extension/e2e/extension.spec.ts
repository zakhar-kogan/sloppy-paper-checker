import {chromium, expect, test, type BrowserContext} from "@playwright/test";
import {resolve} from "node:path";

let context: BrowserContext | undefined;
let extensionId: string;

test.beforeAll(async () => {
  const extensionPath = resolve(import.meta.dirname, "../dist");
  context = await chromium.launchPersistentContext("", {
    channel: "chromium",
    headless: true,
    args: [`--disable-extensions-except=${extensionPath}`, `--load-extension=${extensionPath}`],
  });
  let worker = context.serviceWorkers()[0];
  if (!worker) worker = await context.waitForEvent("serviceworker");
  extensionId = new URL(worker.url()).host;
});

test.afterAll(async () => { await context?.close(); });

test("publisher metadata appears in a persistent extension panel page", async () => {
  if (!context) throw new Error("Extension browser context did not start");
  const publisher = await context.newPage();
  await publisher.goto("http://127.0.0.1:8787/publisher.html");
  const panelPromise = context.waitForEvent("page");
  const worker = context.serviceWorkers()[0];
  await worker.evaluate(async () => {
    const [publisherTab] = await chrome.tabs.query({url: "http://127.0.0.1:8787/*"});
    if (!publisherTab.id) throw new Error("Publisher fixture tab was not found");
    await chrome.tabs.update(publisherTab.id, {active: true});
    await chrome.tabs.create({url: chrome.runtime.getURL("sidepanel.html"), active: false});
  });
  const panel = await panelPromise;
  await expect(panel.getByRole("heading", {name: "Transparent Methods in Widget Science"})).toBeVisible();
  await expect(panel.getByText("10.5555/widget.2026.1")).toBeVisible();
  await expect(panel.getByRole("button", {name: /Analyze current paper/})).toBeEnabled();
});

test("manifest uses temporary broad host access", async () => {
  if (!context) throw new Error("Extension browser context did not start");
  const page = await context.newPage();
  await page.goto(`chrome-extension://${extensionId}/manifest.json`);
  const manifest = JSON.parse(await page.locator("body").innerText());
  expect(manifest.host_permissions).not.toContain("<all_urls>");
  expect(manifest.optional_host_permissions).toContain("https://*/*");
  expect(manifest.permissions).toContain("sidePanel");
});

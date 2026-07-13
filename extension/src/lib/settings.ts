export type LocalSettings = { apiBase: string; apiToken: string };

const DEFAULT_BASE = "http://127.0.0.1:8787";

export async function getLocalSettings(): Promise<LocalSettings> {
  const local = await chrome.storage.local.get("apiBase");
  const session = await chrome.storage.session.get("apiToken");
  return {
    apiBase: typeof local.apiBase === "string" ? local.apiBase : DEFAULT_BASE,
    apiToken: typeof session.apiToken === "string" ? session.apiToken : "",
  };
}

export async function saveLocalSettings(settings: LocalSettings): Promise<void> {
  const url = new URL(settings.apiBase);
  if (!["http:", "https:"].includes(url.protocol)) throw new Error("Endpoint must use HTTP(S)");
  await chrome.storage.local.set({ apiBase: url.toString().replace(/\/$/, "") });
  await chrome.storage.session.set({ apiToken: settings.apiToken });
}

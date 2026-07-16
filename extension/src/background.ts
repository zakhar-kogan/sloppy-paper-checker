const WEB_APP_URL = import.meta.env.VITE_WEB_APP_URL || "http://127.0.0.1:5173/";

function detectPaper(): string {
  const meta = (names: string[]) => {
    for (const name of names) {
      const node = document.querySelector<HTMLMetaElement>(`meta[name="${CSS.escape(name)}"],meta[property="${CSS.escape(name)}"]`);
      if (node?.content) return node.content.trim();
    }
    return "";
  };
  const rawDoi = meta(["citation_doi", "dc.identifier", "DC.Identifier", "prism.doi"]);
  const doiMatch = (rawDoi || document.body.innerText.slice(0, 50000)).match(/10\.\d{4,9}\/[\w.()/:;-]+/i);
  return doiMatch?.[0]?.replace(/[.,;)]$/, "") || location.href;
}

chrome.action.onClicked.addListener(async (tab) => {
  if (!tab.id || !tab.url || /^(chrome:|chrome-extension:)/.test(tab.url)) return;
  let paper = tab.url;
  try {
    const [result] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: detectPaper });
    paper = result.result || paper;
  } catch {
    // Built-in PDF viewers and restricted pages still hand off their URL.
  }
  const target = new URL(WEB_APP_URL);
  target.searchParams.set("paper", paper);
  await chrome.tabs.create({ url: target.toString() });
});

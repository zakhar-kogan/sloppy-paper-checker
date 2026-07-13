import type { PaperCandidate } from "./lib/types";
import {getLocalSettings} from "./lib/settings";

chrome.runtime.onInstalled.addListener(() => {
  void chrome.sidePanel.setPanelBehavior({openPanelOnActionClick: true});
});

function pageMetadata(): PaperCandidate {
  const meta = (names: string[]) => {
    for (const name of names) {
      const node = document.querySelector<HTMLMetaElement>(`meta[name="${CSS.escape(name)}"],meta[property="${CSS.escape(name)}"]`);
      if (node?.content) return node.content.trim();
    }
    return "";
  };
  const rawDoi = meta(["citation_doi", "dc.identifier", "DC.Identifier", "prism.doi"]);
  const doiMatch = (rawDoi || document.body.innerText.slice(0, 50000)).match(/10\.\d{4,9}\/[\w.()/:;-]+/i);
  const title = meta(["citation_title", "dc.title", "og:title"]) || document.title;
  const authors = [...document.querySelectorAll<HTMLMetaElement>('meta[name="citation_author"]')].map(node => node.content).filter(Boolean);
  return {kind: doiMatch ? "doi" : "url", value: doiMatch?.[0] || location.href, title, authors, isPdf: false};
}

async function activeCandidate(): Promise<PaperCandidate> {
  const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
  if (!tab?.id || !tab.url) throw new Error("No readable active tab");
  if (/^(blob:|file:|chrome:|chrome-extension:)/.test(tab.url)) {
    return {kind: "url", value: tab.url, isPdf: true, captureLimitation: "This page cannot be fetched reliably. Upload the PDF instead."};
  }
  const looksPdf = /\.pdf(?:$|[?#])/i.test(tab.url) || tab.url.includes("/pdf/");
  if (looksPdf) return {kind: "url", value: tab.url, title: tab.title, isPdf: true};
  const [result] = await chrome.scripting.executeScript({target: {tabId: tab.id}, func: pageMetadata});
  return result.result as PaperCandidate;
}

async function capturePdf(url: string): Promise<{uploadId: string}> {
  const origin = new URL(url).origin + "/*";
  const granted = await chrome.permissions.request({origins: [origin]});
  if (!granted) throw new Error("Temporary site permission was not granted; upload the PDF instead.");
  try {
    const response = await fetch(url, {credentials: "include", redirect: "follow"});
    if (!response.ok) throw new Error(`PDF capture returned ${response.status}`);
    const blob = await response.blob();
    const prefix = new Uint8Array(await blob.slice(0, 5).arrayBuffer());
    if (new TextDecoder().decode(prefix) !== "%PDF-") throw new Error("Captured resource was not a PDF");
    if (blob.size > 25 * 1024 * 1024) throw new Error("PDF is larger than 25 MB");
    const {apiBase, apiToken} = await getLocalSettings();
    if (!apiToken) throw new Error("Open settings and enter the backend access token first.");
    const form = new FormData();
    form.append("file", blob, new URL(url).pathname.split("/").pop() || "paper.pdf");
    const upload = await fetch(`${apiBase}/v1/uploads`, {
      method: "POST",
      headers: {Authorization: `Bearer ${apiToken}`},
      body: form,
      credentials: "omit",
      redirect: "error"
    });
    if (!upload.ok) throw new Error(`Backend upload returned ${upload.status}`);
    const receipt = await upload.json() as {id?: string};
    if (!receipt.id) throw new Error("Backend did not return an upload identifier");
    return {uploadId: receipt.id};
  } finally {
    await chrome.permissions.remove({origins: [origin]});
  }
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "GET_ACTIVE_PAPER") {
    void activeCandidate().then(sendResponse, error => sendResponse({error: String(error.message || error)}));
    return true;
  }
  if (message?.type === "CAPTURE_PDF" && typeof message.url === "string") {
    void capturePdf(message.url).then(sendResponse, error => sendResponse({error: String(error.message || error)}));
    return true;
  }
  if (message?.type === "SET_BADGE") {
    const score = Math.round(Number(message.score));
    void chrome.action.setBadgeBackgroundColor({color: score >= 70 ? "#2f6f5e" : score >= 40 ? "#b56b2a" : "#a83f35"});
    void chrome.action.setBadgeText({text: String(score)});
  }
  return false;
});

import {useEffect, useState} from "react";
import {createRoot} from "react-dom/client";
import {getLocalSettings, saveLocalSettings} from "./lib/settings";
import "./style.css";

type Provider = {base_url: string; has_api_key: boolean; api_key_source: string; planner_model: string; worker_model: string; critic_model: string; default_depth: string; max_concurrency: number; sequential_mode: boolean; max_cost_usd: number; retention_days: number};

function App() {
  const [apiBase, setApiBase] = useState(""); const [apiToken, setApiToken] = useState("");
  const [provider, setProvider] = useState<Provider | null>(null); const [providerKey, setProviderKey] = useState("");
  const [models, setModels] = useState<string[]>([]); const [notice, setNotice] = useState("");
  useEffect(() => { void getLocalSettings().then(s => {setApiBase(s.apiBase); setApiToken(s.apiToken);}); }, []);

  async function backend(path: string, init: RequestInit = {}) {
    const headers = new Headers(init.headers); headers.set("Authorization", `Bearer ${apiToken}`); if (init.body) headers.set("Content-Type", "application/json");
    const response = await fetch(`${apiBase}${path}`, {...init, headers, credentials: "omit"});
    const data = await response.json().catch(() => ({})); if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`); return data;
  }
  async function connect() { try { await saveLocalSettings({apiBase, apiToken}); const data = await backend("/v1/settings"); setProvider(data); setNotice("Backend connected. The access token is held for this browser session only."); } catch (e) {setNotice(e instanceof Error ? e.message : String(e));} }
  async function saveProvider() { if (!provider) return; try { const payload = {...provider, api_key: providerKey || null}; delete (payload as Partial<Provider>).has_api_key; delete (payload as Partial<Provider>).api_key_source; const data = await backend("/v1/settings", {method: "PUT", body: JSON.stringify(payload)}); setProvider(data); setProviderKey(""); setNotice("Provider settings saved encrypted on the backend."); } catch (e) {setNotice(e instanceof Error ? e.message : String(e));} }
  async function discover() { try { const data = await backend("/v1/providers/models"); setModels(data.models.map((m: {id: string}) => m.id)); } catch (e) {setNotice(e instanceof Error ? e.message : String(e));} }
  return <main className="settings-page"><header><div className="mark">SPC<span>β</span></div><p className="eyebrow">operator console</p><h1>Analysis settings</h1><p>Network credentials never enter paper prompts or extension reports.</p></header>
    {notice && <div className="banner">{notice}</div>}
    <section><h2>Backend connection</h2><label>API endpoint<input value={apiBase} onChange={e => setApiBase(e.target.value)} placeholder="http://127.0.0.1:8787"/></label><label>Access token <small>(session only)</small><input type="password" value={apiToken} onChange={e => setApiToken(e.target.value)}/></label><button onClick={() => void connect()}>Connect</button></section>
    {provider && <section><div className="section-row"><h2>Model provider</h2><span className="status-chip">{provider.has_api_key ? provider.api_key_source : "key missing"}</span></div><label>OpenAI-compatible base URL<input value={provider.base_url} onChange={e => setProvider({...provider, base_url: e.target.value})}/></label><label>API key <small>(leave blank to preserve)</small><input type="password" value={providerKey} onChange={e => setProviderKey(e.target.value)}/></label><div className="model-grid">{["planner_model", "worker_model", "critic_model"].map(key => <label key={key}>{key.replace("_", " ")}<input list="models" value={String(provider[key as keyof Provider])} onChange={e => setProvider({...provider, [key]: e.target.value})}/></label>)}</div><datalist id="models">{models.map(model => <option value={model} key={model}/>)}</datalist><div className="button-row"><button onClick={() => void saveProvider()}>Save provider</button><button className="secondary" onClick={() => void discover()}>Discover /v1/models</button></div><p className="limitation">Nebius Token Factory is the default. For sensitive manuscripts, enable the provider’s Zero Data Retention controls; retention may otherwise occur.</p></section>}
  </main>;
}
createRoot(document.getElementById("root")!).render(<App/>);


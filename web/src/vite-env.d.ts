/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_SHOW_EXAMPLES: string;
  readonly VITE_LIVE_ANALYSIS_ENABLED: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

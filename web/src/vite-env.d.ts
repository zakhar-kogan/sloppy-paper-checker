/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_STATIC_SHOWCASE: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

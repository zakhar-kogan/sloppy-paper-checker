import { defineConfig } from "vite";
import preact from "@preact/preset-vite";

export default defineConfig(({ mode }) => {
  const pages = mode === "pages";
  return {
    base: pages ? "/sloppy-paper-checker/" : "/",
    define: {
      "import.meta.env.VITE_STATIC_SHOWCASE": JSON.stringify(pages ? "true" : "false"),
    },
    plugins: [preact()],
    server: {
      port: 5173,
      proxy: {
        "/v1": "http://127.0.0.1:8787",
        "/healthz": "http://127.0.0.1:8787",
      },
    },
  };
});

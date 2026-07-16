import js from "@eslint/js";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  {ignores: ["dist", "coverage", "vite.config.js", "vite.config.d.ts"]},
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: {ecmaVersion: 2022, globals: {...globals.browser, chrome: "readonly"}},
    rules: {
      "@typescript-eslint/no-explicit-any": "error"
    }
  }
);

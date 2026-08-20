import js from "@eslint/js";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: [".next/**", "node_modules/**", "next-env.d.ts"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    languageOptions: {
      globals: {
        window: "readonly",
        localStorage: "readonly",
        fetch: "readonly",
        WebSocket: "readonly",
        RequestInit: "readonly",
        Response: "readonly",
        process: "readonly",
        console: "readonly",
      },
    },
    rules: {
      // The API's shapes are declared in src/lib/types.ts. An `any` here would
      // put a hole in exactly the boundary those types exist to close.
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
    },
  },
);

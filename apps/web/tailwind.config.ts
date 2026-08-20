import type { Config } from "tailwindcss";

export default {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#0d1117",
        surface: "#161b22",
        line: "#26303d",
        muted: "#8b949e",
        accent: "#4493f8",
        pass: "#3fb950",
        warn: "#d29922",
        fail: "#f85149",
      },
      fontFamily: {
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
} satisfies Config;

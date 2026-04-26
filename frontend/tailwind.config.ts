import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        // Backgrounds
        bg:            "#f8f9fa",
        "bg-card":     "#ffffff",
        "bg-hover":    "#f1f5f9",
        "bg-active":   "#eff6ff",

        // Borders
        "border-color":        "#e2e8f0",
        "border-color-subtle": "#f1f5f9",

        // Text
        "text-primary":   "#0f172a",
        "text-secondary": "#64748b",
        "text-tertiary":  "#94a3b8",
        "text-mono":      "#475569",

        // Accent (blue)
        accent:       "#2563eb",
        "accent-dim": "#1d4ed8",
        "accent-bg":  "#eff6ff",

        // Node type colors
        node: {
          person:       "#2563eb",
          organization: "#7c3aed",
          document:     "#d97706",
          message:      "#0891b2",
          event:        "#db2777",
          asset:        "#059669",
          topic:        "#ea580c",
        },

        // Confidence
        "conf-high": "#16a34a",
        "conf-mid":  "#d97706",
        "conf-low":  "#dc2626",

        // Human provenance
        "human-prov": "#7c3aed",

        // Legacy dark tokens (hero page still uses these)
        background:       "#0d1117",
        surface:          "#161b22",
        "surface-raised": "#21262d",
        border:           "#30363d",
      },
      fontFamily: {
        sans: ["var(--font-geist-sans)", "system-ui", "sans-serif"],
        mono: ["var(--font-geist-mono)", "ui-monospace", "monospace"],
      },
      animation: {
        "fade-in":  "fadeIn 0.15s ease-out",
        "slide-up": "slideUp 0.2s ease-out",
      },
      keyframes: {
        fadeIn:  { from: { opacity: "0" }, to: { opacity: "1" } },
        slideUp: { from: { transform: "translateY(6px)", opacity: "0" }, to: { transform: "translateY(0)", opacity: "1" } },
      },
    },
  },
  plugins: [],
};
export default config;

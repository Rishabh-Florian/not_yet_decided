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
        bg:            "#080808",
        "bg-card":     "#111111",
        "bg-hover":    "#181818",
        "bg-active":   "#202020",

        // Borders
        "border-color":        "#2a2a2a",
        "border-color-subtle": "#1c1c1c",

        // Text
        "text-primary":   "#f3f3f3",
        "text-secondary": "#b4b4b4",
        "text-tertiary":  "#7c7c7c",
        "text-mono":      "#9a9a9a",

        // Accent (monochrome)
        accent:       "#e5e5e5",
        "accent-dim": "#cfcfcf",
        "accent-bg":  "#1b1b1b",

        // Node type colors
        node: {
          person:       "#f0f0f0",
          organization: "#dadada",
          document:     "#c8c8c8",
          message:      "#b5b5b5",
          event:        "#a8a8a8",
          asset:        "#989898",
          topic:        "#888888",
        },

        // Confidence
        "conf-high": "#f0f0f0",
        "conf-mid":  "#b8b8b8",
        "conf-low":  "#7f7f7f",

        // Human provenance
        "human-prov": "#d5d5d5",

        // Legacy dark tokens (hero page still uses these)
        background:       "#090909",
        surface:          "#121212",
        "surface-raised": "#1a1a1a",
        border:           "#2a2a2a",
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

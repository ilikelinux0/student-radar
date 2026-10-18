import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  test: {
    environment: "node",
    // Vitest stubs CSS to an empty string by default. The cascade test reads
    // index.css?raw, so that one file has to come through.
    css: { include: [/index\.css/] },
  },
});

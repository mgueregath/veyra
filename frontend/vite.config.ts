import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// La API corre aparte (por defecto en :8910); el proxy evita CORS y mantiene la cookie de sesión.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": process.env.LABELHUB_API ?? "http://127.0.0.1:8910" } },
  test: { environment: "node" },
});

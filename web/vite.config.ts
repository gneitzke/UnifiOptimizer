import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  define: {
    __APP_VERSION__: JSON.stringify(
      `0.2.${new Date().toISOString().slice(0, 16).replace(/[-T:]/g, '.')}`
    ),
  },
  server: {
    headers: {
      // Always serve fresh app code during development
      'Cache-Control': 'no-store',
    },
    proxy: {
      '/api': 'http://localhost:8000',
      '/ws': { target: 'ws://localhost:8000', ws: true },
    },
  },
})

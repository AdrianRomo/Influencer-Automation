/// <reference types="vitest" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Proxy all API paths to the FastAPI backend so cookies work in dev.
// Set VITE_API_BASE_URL to override with an absolute URL (bypasses proxy).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '^/(auth|users|sources|platforms|articles|generate-video|generate|jobs|audio|image|video|thumbnail|scene-videos|rss|health|admin|beat)': {
        target: 'http://localhost:8085',
        changeOrigin: true,
      },
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    css: false,
  },
})

import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// If you prefer not to set VITE_API_BASE_URL, you can proxy requests to your backend.
// Uncomment and set the target to match your docker-compose service name / port.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    // proxy: {
    //   '/': {
    //     target: 'http://api:8000',
    //     changeOrigin: true,
    //     secure: false,
    //   },
    // },
  },
})

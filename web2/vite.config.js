import path from 'path'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, 'src'),
    },
  },
  server: {
    // 8300 is the only backend now — app.py serves web2/dist, so V1 is no
    // longer a separate stack worth reserving a port for. It is also the port
    // the Google OAuth client's redirect URI is registered against.
    port: 5174,
    proxy: {
      '/api': 'http://localhost:8300',
      '/health': 'http://localhost:8300',
    },
  },
})

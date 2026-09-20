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
    // Dev only; the server itself serves web2/dist. CAIRN_API points the
    // proxy at a server on another port — it must match that server's PORT.
    port: 5174,
    proxy: {
      '/api': process.env.CAIRN_API || 'http://localhost:8300',
      '/health': process.env.CAIRN_API || 'http://localhost:8300',
    },
  },
})

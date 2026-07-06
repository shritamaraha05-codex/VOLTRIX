import { defineConfig } from 'vite'

export default defineConfig({
  root: '.',
  server: {
    port: 5173,
    proxy: {
      '/health': 'http://localhost:8000',
      '/zones': 'http://localhost:8000',
      '/stress-events': 'http://localhost:8000',
      '/recommendations': 'http://localhost:8000',
      '/simulation': 'http://localhost:8000',
      '/simulate': 'http://localhost:8000',
      '/chat': 'http://localhost:8000',
      '/ingest': 'http://localhost:8000',
      '/admin': 'http://localhost:8000'
    }
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true
  }
})

import { defineConfig } from 'vite'

export default defineConfig({
  root: '.',
  server: {
    port: 5173,
    proxy: {
      '/health': 'https://voltrix-backend-xbeezmmecq-uc.a.run.app',
      '/zones': 'https://voltrix-backend-xbeezmmecq-uc.a.run.app',
      '/stress-events': 'https://voltrix-backend-xbeezmmecq-uc.a.run.app',
      '/recommendations': 'https://voltrix-backend-xbeezmmecq-uc.a.run.app',
      '/simulation': 'https://voltrix-backend-xbeezmmecq-uc.a.run.app',
      '/simulate': 'https://voltrix-backend-xbeezmmecq-uc.a.run.app',
      '/chat': 'https://voltrix-backend-xbeezmmecq-uc.a.run.app',
      '/ingest': 'https://voltrix-backend-xbeezmmecq-uc.a.run.app',
      '/admin': 'https://voltrix-backend-xbeezmmecq-uc.a.run.app'
    }
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true
  }
})

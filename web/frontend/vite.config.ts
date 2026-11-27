import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Разработка: npm run dev (http://localhost:5173) — запросы /api уходят в Django на 127.0.0.1:8000.
// Работа: npm run build → папка dist, её отдаёт сам Django (один адрес, один процесс).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
  build: { outDir: 'dist', emptyOutDir: true },
})

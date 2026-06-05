// Vite 构建配置 — 开发时代理 /api 到后端 8888 端口
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': {
        // target: 'http://192.168.3.222:8888',
        target: 'http://localhost:8888',
        changeOrigin: true,
      },
    },
  },
})

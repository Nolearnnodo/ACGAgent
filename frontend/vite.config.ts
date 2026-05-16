import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Vite 配置：dev 模式启用 HMR，docker 内通过 proxy 反代后端 API。
export default defineConfig({
  plugins: [vue()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    // 后端 API 反代：浏览器看到 /api/* → 容器内转发到 backend:8000
    proxy: {
      '/api': {
        target: 'http://backend:8000',
        changeOrigin: true,
      },
    },
    // docker bind mount + Windows / macOS 文件系统事件不可靠，用 polling 监听变更
    watch: {
      usePolling: true,
      interval: 500,
    },
    // 允许从主机 localhost 与容器内服务名访问
    allowedHosts: ['localhost', '127.0.0.1', 'frontend', 'acgagent-frontend'],
  },
})

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Vite 配置保持精简，优先保证前端骨架可运行。
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
  },
})

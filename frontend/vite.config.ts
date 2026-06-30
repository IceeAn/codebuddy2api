import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  plugins: [vue(), tailwindcss()],
  server: {
    proxy: {
      '/auth': 'http://127.0.0.1:8001',
      '/api': 'http://127.0.0.1:8001',
      '/codebuddy': 'http://127.0.0.1:8001',
      '/health': 'http://127.0.0.1:8001',
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: false,
    chunkSizeWarningLimit: 700,
    rollupOptions: {
      output: {
        // 自建 UI 组件与 @lucide/vue 不单独分包，交由 Rollup tree-shake 后并入主 chunk
        codeSplitting: {
          groups: [
            {
              name: 'vue',
              test: /node_modules[\\/](vue|vue-router|pinia|@tanstack[\\/]vue-query)[\\/]/,
            },
          ],
        },
      },
    },
  },
});

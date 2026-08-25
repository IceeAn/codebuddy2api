import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { defineConfig, type Plugin } from 'vite';
import vue from '@vitejs/plugin-vue';
import tailwindcss from '@tailwindcss/vite';

const THEME_INIT_SOURCE_URL = '/src/theme-init.js';

export function createHashedAssetFileName(
  baseName: string,
  extension: string,
  source: string,
): string {
  const contentHash = createHash('sha256').update(source).digest('hex').slice(0, 12);
  return `assets/${baseName}-${contentHash}.${extension}`;
}

export function rewriteThemeInitAssetUrl(html: string, assetFileName: string): string {
  const parts = html.split(THEME_INIT_SOURCE_URL);
  if (parts.length !== 2) {
    throw new Error('主题初始化脚本引用必须恰好出现一次');
  }
  return `${parts[0]}/${assetFileName}${parts[1]}`;
}

function themeInitAssetPlugin(): Plugin {
  let asset: { fileName: string; source: string } | undefined;

  function loadAsset() {
    if (asset) return asset;
    const source = readFileSync(new URL('./src/theme-init.js', import.meta.url), 'utf8');
    asset = {
      fileName: createHashedAssetFileName('theme-init', 'js', source),
      source,
    };
    return asset;
  }

  return {
    name: 'theme-init-asset',
    apply: 'build',
    transformIndexHtml: {
      order: 'pre',
      handler(html) {
        return rewriteThemeInitAssetUrl(html, loadAsset().fileName);
      },
    },
    generateBundle() {
      const { fileName, source } = loadAsset();
      this.emitFile({
        type: 'asset',
        fileName,
        source,
      });
    },
  };
}

export default defineConfig({
  plugins: [themeInitAssetPlugin(), vue(), tailwindcss()],
  server: {
    proxy: {
      '/auth': 'http://127.0.0.1:8001',
      '/api': 'http://127.0.0.1:8001',
      '/codebuddy': 'http://127.0.0.1:8001',
      '/openai': 'http://127.0.0.1:8001',
      '/anthropic': 'http://127.0.0.1:8001',
      '/health': 'http://127.0.0.1:8001',
      '/docs': 'http://127.0.0.1:8001',
      '/redoc': 'http://127.0.0.1:8001',
      '/openapi.json': 'http://127.0.0.1:8001',
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

import { resolve, sep } from 'node:path';
import type { Plugin } from 'vite';
import { buildUsageDocs, PUBLIC_ATTACHMENTS, type UsageDocsBuild } from './usageDocs.ts';

const MODULE_ID = 'virtual:usage-docs';
const RESOLVED_ID = `\0${MODULE_ID}`;

/** 构建与开发共用同一份文档编译结果，浏览器不接触本地文件系统。 */
export function usageDocsPlugin(repositoryRoot: string): Plugin {
  const root = resolve(repositoryRoot);
  let compiled: UsageDocsBuild | undefined;
  const compile = () => (compiled ??= buildUsageDocs(root));
  return {
    name: 'usage-documentation',
    buildStart() {
      compiled = undefined;
      for (const file of compile().files) this.addWatchFile(file);
    },
    resolveId(id) {
      if (id === MODULE_ID) return RESOLVED_ID;
    },
    load(id) {
      if (id === RESOLVED_ID) return `export default ${JSON.stringify(compile().documents)};`;
    },
    generateBundle() {
      for (const { fileName, source } of compile().assets) {
        this.emitFile({ type: 'asset', fileName, source });
      }
    },
    configureServer(server) {
      const documentDirectory = resolve(root, 'doc');
      const watchedFiles = ['README.md', ...PUBLIC_ATTACHMENTS].map((path) => resolve(root, path));
      server.watcher.add([documentDirectory, ...watchedFiles]);
      server.watcher.on('all', (_event, path) => {
        const absolute = resolve(path);
        if (
          absolute !== documentDirectory &&
          !absolute.startsWith(`${documentDirectory}${sep}`) &&
          !watchedFiles.includes(absolute)
        )
          return;
        compiled = undefined;
        const module = server.moduleGraph.getModuleById(RESOLVED_ID);
        if (module) server.moduleGraph.invalidateModule(module);
        server.ws.send({ type: 'full-reload' });
      });
      server.middlewares.use((request, response, next) => {
        if (!request.url?.startsWith('/assets/usage-')) return next();
        const path = request.url.split('?')[0];
        const asset = compile().assets.find((item) => `/${item.fileName}` === path);
        if (!asset) return next();
        response.setHeader('Content-Type', asset.mediaType);
        response.setHeader('Cache-Control', 'no-cache');
        response.end(request.method === 'HEAD' ? undefined : asset.source);
      });
    },
  };
}

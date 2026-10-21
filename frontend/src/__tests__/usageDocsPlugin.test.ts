import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { usageDocsPlugin } from '../../tooling/usageDocsPlugin';

let root: string;
beforeEach(() => {
  root = mkdtempSync(join(tmpdir(), 'usage-plugin-test-'));
  mkdirSync(join(root, 'doc'));
  writeFileSync(join(root, 'README.md'), '# 项目\n\n[示例](doc/config.json)');
  writeFileSync(join(root, 'doc/config.json'), '{}');
});
afterEach(() => rmSync(root, { recursive: true, force: true }));

it('生成浏览器文档模块并将附件交给 Vite 打包', () => {
  const plugin = usageDocsPlugin(root) as any;
  const context = {
    addWatchFile: vi.fn<(path: string) => void>(),
    emitFile: vi.fn<(asset: unknown) => void>(),
  };
  plugin.buildStart.call(context);
  expect(context.addWatchFile).toHaveBeenCalledWith(join(root, 'README.md'));
  expect(plugin.resolveId('other')).toBeUndefined();
  const id = plugin.resolveId('virtual:usage-docs');
  expect(plugin.load('other')).toBeUndefined();
  expect(plugin.load(id)).toContain('项目');
  plugin.generateBundle.call(context);
  expect(context.emitFile).toHaveBeenCalledWith(
    expect.objectContaining({
      type: 'asset',
      fileName: expect.stringMatching(/^assets\/usage-.*\.json$/),
    }),
  );
});

it('开发服务器提供同源附件，并在文档新增、删除和更新时刷新模块', () => {
  const plugin = usageDocsPlugin(root) as any;
  const server = {
    watcher: {
      add: vi.fn<(paths: string[]) => void>(),
      on: vi.fn<(event: string, callback: (event: string, path: string) => void) => void>(),
    },
    middlewares: {
      use: vi.fn<(handler: (request: any, response: any, next: () => void) => void) => void>(),
    },
    moduleGraph: {
      getModuleById: vi.fn<(id: string) => { id: string } | undefined>(),
      invalidateModule: vi.fn<(module: unknown) => void>(),
    },
    ws: { send: vi.fn<(message: unknown) => void>() },
  };
  plugin.configureServer(server);
  const middleware = server.middlewares.use.mock.calls[0]![0];
  const next = vi.fn<() => void>();
  const response = {
    setHeader: vi.fn<(name: string, value: string) => void>(),
    end: vi.fn<(body?: Buffer) => void>(),
  };
  middleware({}, response, next);
  middleware({ url: '/assets/usage-missing.json' }, response, next);
  expect(next).toHaveBeenCalledTimes(2);
  const module = plugin.load(plugin.resolveId('virtual:usage-docs'));
  const url = module.match(/\/assets\/usage-[a-f0-9]+\.json/)[0];
  middleware({ url: `${url}?v=1`, method: 'GET' }, response, next);
  expect(response.end).toHaveBeenLastCalledWith(Buffer.from('{}'));
  middleware({ url, method: 'HEAD' }, response, next);
  expect(response.end).toHaveBeenLastCalledWith(undefined);
  const change = server.watcher.on.mock.calls[0]![1];
  change('change', join(root, 'config.py'));
  expect(server.ws.send).not.toHaveBeenCalled();
  change('add', join(root, 'doc/new.md'));
  expect(server.ws.send).toHaveBeenCalledWith({ type: 'full-reload' });
  server.moduleGraph.getModuleById.mockReturnValue({ id: 'module' });
  writeFileSync(join(root, 'README.md'), '# 新标题');
  change('change', join(root, 'README.md'));
  expect(server.moduleGraph.invalidateModule).toHaveBeenCalledWith({ id: 'module' });
  expect(plugin.load(plugin.resolveId('virtual:usage-docs'))).toContain('新标题');
  change('unlink', join(root, 'doc/config.json'));
});

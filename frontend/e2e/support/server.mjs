import { createServer } from 'node:http';
import { mkdtemp, readFile, readdir, rm } from 'node:fs/promises';
import { extname, join, resolve, sep } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import vue from '@vitejs/plugin-vue';
import { build } from 'vite';

const port = 4174;
const MAX_DOCUMENT_DELAY_MS = 2_000;
const fixtureRoot = fileURLToPath(new URL('../fixture', import.meta.url));
const temporaryRoot = await mkdtemp(join(tmpdir(), 'codebuddy2api-chunk-e2e-'));
const builds = {
  a: join(temporaryRoot, 'a'),
  b: join(temporaryRoot, 'b'),
};

async function buildVersion(version, outDir) {
  await build({
    root: fixtureRoot,
    configFile: false,
    logLevel: 'error',
    plugins: [vue()],
    define: { __E2E_VERSION__: JSON.stringify(version.toUpperCase()) },
    build: {
      outDir,
      emptyOutDir: true,
      rollupOptions: {
        output: {
          entryFileNames: `assets/[name]-${version}-[hash].js`,
          chunkFileNames: `assets/[name]-${version}-[hash].js`,
          assetFileNames: `assets/[name]-${version}-[hash][extname]`,
        },
      },
    },
  });
}

await Promise.all([buildVersion('a', builds.a), buildVersion('b', builds.b)]);
const targetChunkB = (await readdir(join(builds.b, 'assets'))).find((name) =>
  name.startsWith('TargetPage-b-'),
);
if (targetChunkB === undefined) throw new Error('未找到 B 版本目标页 chunk');

let phase = 'a';
let targetMissing = false;
let documentDelayMs = 0;
let documentRequests = 0;

function sendJson(response, status, value) {
  response.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' });
  response.end(JSON.stringify(value));
}

function contentType(pathname) {
  switch (extname(pathname)) {
    case '.html':
      return 'text/html; charset=utf-8';
    case '.js':
      return 'text/javascript; charset=utf-8';
    case '.css':
      return 'text/css; charset=utf-8';
    default:
      return 'application/octet-stream';
  }
}

const server = createServer(async (request, response) => {
  try {
    const url = new URL(request.url ?? '/', `http://127.0.0.1:${port}`);
    if (url.pathname === '/__control/state') {
      sendJson(response, 200, { phase, targetMissing, documentDelayMs, documentRequests });
      return;
    }
    if (request.method === 'POST' && url.pathname === '/__control/reset') {
      phase = 'a';
      targetMissing = false;
      documentDelayMs = 0;
      documentRequests = 0;
      sendJson(response, 200, { ok: true });
      return;
    }
    if (request.method === 'POST' && url.pathname === '/__control/switch') {
      const requestedDelay = Number(url.searchParams.get('document-delay-ms') ?? '0');
      if (
        !Number.isSafeInteger(requestedDelay) ||
        requestedDelay < 0 ||
        requestedDelay > MAX_DOCUMENT_DELAY_MS
      ) {
        sendJson(response, 400, {
          error: `document-delay-ms 必须是 0 到 ${MAX_DOCUMENT_DELAY_MS} 的安全整数`,
        });
        return;
      }
      phase = 'b';
      targetMissing = url.searchParams.get('missing') === '1';
      documentDelayMs = requestedDelay;
      sendJson(response, 200, { ok: true });
      return;
    }

    const buildRoot = builds[phase];
    let pathname = decodeURIComponent(url.pathname);
    if (pathname === '/' || pathname === '/index.html') {
      pathname = '/index.html';
      documentRequests += 1;
      if (documentDelayMs > 0) {
        await new Promise((resolvePromise) => setTimeout(resolvePromise, documentDelayMs));
      }
    }
    if (targetMissing && pathname === `/assets/${targetChunkB}`) {
      response.writeHead(404).end();
      return;
    }

    const filePath = resolve(buildRoot, `.${pathname}`);
    if (filePath !== buildRoot && !filePath.startsWith(`${buildRoot}${sep}`)) {
      response.writeHead(400).end();
      return;
    }
    const body = await readFile(filePath);
    response.writeHead(200, {
      'Content-Type': contentType(pathname),
      'Cache-Control': pathname === '/index.html' ? 'no-store' : 'public, max-age=31536000',
    });
    response.end(body);
  } catch (error) {
    if (error && typeof error === 'object' && 'code' in error && error.code === 'ENOENT') {
      response.writeHead(404).end();
      return;
    }
    console.error(error);
    response.writeHead(500).end();
  }
});

server.listen(port, '127.0.0.1', () => {
  console.log(`chunk 恢复 E2E 服务已监听 ${port}`);
});

async function shutdown() {
  await new Promise((resolvePromise) => server.close(resolvePromise));
  await rm(temporaryRoot, { recursive: true, force: true });
}

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.once(signal, () => {
    void shutdown().finally(() => process.exit(0));
  });
}

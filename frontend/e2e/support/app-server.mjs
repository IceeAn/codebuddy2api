import { createServer } from 'node:http';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { extname, join, resolve, sep } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { build } from 'vite';

const port = 4175;
const frontendRoot = fileURLToPath(new URL('../../', import.meta.url));
const buildRoot = await mkdtemp(join(tmpdir(), 'codebuddy2api-app-e2e-'));

await build({
  root: frontendRoot,
  logLevel: 'error',
  build: {
    outDir: buildRoot,
    emptyOutDir: true,
  },
});

let mode = 'bootstrap';
let currentPassword = 'admin';
let settingsRequests = 0;
let adminRequests = 0;
let passwordChanges = [];

function reset(nextMode) {
  mode = nextMode === 'formal' ? 'formal' : 'bootstrap';
  currentPassword = mode === 'formal' ? 'formal-password' : 'admin';
  settingsRequests = 0;
  adminRequests = 0;
  passwordChanges = [];
}

function sendJson(response, status, value, headers = {}) {
  response.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
    ...headers,
  });
  response.end(JSON.stringify(value));
}

async function readJson(request) {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  return JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}');
}

function sessionBody(forced) {
  return {
    authenticated: true,
    username: 'admin',
    source: 'session_cookie',
    password_change_required: forced,
  };
}

function contentType(pathname) {
  switch (extname(pathname)) {
    case '.html':
      return 'text/html; charset=utf-8';
    case '.js':
      return 'text/javascript; charset=utf-8';
    case '.css':
      return 'text/css; charset=utf-8';
    case '.svg':
      return 'image/svg+xml';
    case '.woff2':
      return 'font/woff2';
    default:
      return 'application/octet-stream';
  }
}

async function serveFile(pathname, response) {
  const relativePath =
    pathname === '/' || pathname === '/index.html' ? 'index.html' : pathname.slice(1);
  const filePath = resolve(buildRoot, relativePath);
  if (filePath !== buildRoot && !filePath.startsWith(`${buildRoot}${sep}`)) {
    response.writeHead(400).end();
    return;
  }
  const body = await readFile(filePath);
  response.writeHead(200, {
    'Content-Type': contentType(relativePath),
    'Cache-Control': pathname === '/' || pathname === '/index.html' ? 'no-store' : 'public',
  });
  response.end(body);
}

const server = createServer(async (request, response) => {
  try {
    const url = new URL(request.url ?? '/', `http://127.0.0.1:${port}`);
    if (url.pathname === '/__app_control/state') {
      sendJson(response, 200, {
        mode,
        settingsRequests,
        adminRequests,
        passwordChanges,
      });
      return;
    }
    if (request.method === 'POST' && url.pathname === '/__app_control/reset') {
      reset(url.searchParams.get('mode'));
      sendJson(response, 200, { ok: true });
      return;
    }
    if (url.pathname === '/auth/bootstrap-status') {
      sendJson(response, 200, {
        bootstrap_required: mode === 'bootstrap' || mode === 'forced',
        bootstrap_expired: false,
      });
      return;
    }
    if (url.pathname === '/auth/session') {
      if (mode === 'forced') {
        sendJson(response, 200, sessionBody(true));
      } else if (mode === 'formal') {
        sendJson(response, 200, sessionBody(false));
      } else {
        sendJson(
          response,
          401,
          { detail: 'Invalid authentication credentials' },
          { 'WWW-Authenticate': 'Bearer' },
        );
      }
      return;
    }
    if (request.method === 'POST' && url.pathname === '/auth/login') {
      const body = await readJson(request);
      if (mode === 'bootstrap' && body.username === 'admin' && body.password === 'admin') {
        mode = 'forced';
        sendJson(response, 200, sessionBody(true), {
          'Set-Cookie': 'codebuddy2api_session=e2e; Path=/; HttpOnly; SameSite=Lax',
        });
      } else if (
        mode === 'logged_out' &&
        body.username === 'admin' &&
        body.password === currentPassword
      ) {
        mode = 'formal';
        sendJson(response, 200, sessionBody(false));
      } else {
        sendJson(response, 401, { detail: '用户名或密码错误' }, { 'WWW-Authenticate': 'Bearer' });
      }
      return;
    }
    if (request.method === 'POST' && url.pathname === '/auth/change-password') {
      const body = await readJson(request);
      passwordChanges.push(body);
      if (mode !== 'forced' || body.current_password !== undefined) {
        sendJson(response, 400, {
          error_code: 'current_password_not_allowed',
          detail: '首次修改密码时不得提交当前密码',
        });
        return;
      }
      currentPassword = body.new_password;
      mode = 'logged_out';
      sendJson(
        response,
        200,
        { password_changed: true, authenticated: false },
        { 'Set-Cookie': 'codebuddy2api_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax' },
      );
      return;
    }
    if (request.method === 'POST' && url.pathname === '/auth/logout') {
      mode = 'logged_out';
      sendJson(response, 200, { authenticated: false });
      return;
    }
    if (url.pathname === '/api/admin/settings') {
      settingsRequests += 1;
      sendJson(response, 200, { settings: {}, fields: [] });
      return;
    }
    if (url.pathname.startsWith('/api/admin/')) {
      adminRequests += 1;
      sendJson(response, 500, { detail: 'E2E 未配置此管理接口' });
      return;
    }

    await serveFile(url.pathname, response);
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
  console.log(`账号流程 E2E 服务已监听 ${port}`);
});

async function shutdown() {
  await new Promise((resolvePromise) => server.close(resolvePromise));
  await rm(buildRoot, { recursive: true, force: true });
}

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.once(signal, () => {
    void shutdown().finally(() => process.exit(0));
  });
}

/* Client-side failure-path regression tests.
 *
 * Two layers:
 *   1. tests/access-client.js against real local HTTP servers (hang, refused, 401, bad
 *      payload, 500) using Node's global fetch through a VM context.
 *   2. app.js access gating in the same DOM-stub harness style as
 *      tests/client_gates.test.js, asserting that a failing server never unlocks
 *      Premium and never blocks offline learning.
 *
 * Run with: node tests/client_failure_paths.test.js
 */
'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const vm = require('node:vm');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..');

/* ------------------------------------------------------------------ helpers */

function loadAccessClient({ baseUrl, timeoutMs }) {
  const stored = new Map();
  const localStorage = {
    getItem: key => (stored.has(key) ? stored.get(key) : null),
    setItem: (key, value) => stored.set(key, String(value)),
    removeItem: key => stored.delete(key)
  };
  const window = {
    location: { href: `${baseUrl}/index.html`, origin: baseUrl, hostname: '127.0.0.1', protocol: 'http:' },
    EQ_ALLOW_HTTP_DEV: true,
    EQ_API_BASE: baseUrl
  };
  if (typeof timeoutMs === 'number') window.EQ_REQUEST_TIMEOUT_MS = timeoutMs;
  const context = {
    window, localStorage, console, fetch, AbortController, URL,
    setTimeout, clearTimeout, clearInterval, String, Number, Boolean, Object, Array,
    JSON, Error, Promise, Map, Set
  };
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(ROOT, 'access-client.js'), 'utf8'), context,
    { filename: 'access-client.js' });
  return { api: window.EnglishQuestAccess, stored };
}

function startServer(handler) {
  const sockets = new Set();
  const server = http.createServer(handler);
  server.on('connection', socket => {
    sockets.add(socket);
    socket.on('close', () => sockets.delete(socket));
  });
  return new Promise(resolve => {
    server.listen(0, '127.0.0.1', () => {
      resolve({
        server,
        base: `http://127.0.0.1:${server.address().port}`,
        async close() {
          for (const socket of sockets) socket.destroy();
          await new Promise(done => server.close(done));
        }
      });
    });
  });
}

function withDeadline(promise, ms, label) {
  return Promise.race([
    promise,
    new Promise((_, reject) => setTimeout(
      () => reject(new Error(`${label}: timed out after ${ms}ms — the client hung`)), ms))
  ]);
}

/* ------------------------------------------------------------- app harness */

function makeHarness({ api, hasSession = false } = {}) {
  const html = { value: '' };
  const appRoot = {
    addEventListener() {},
    get innerHTML() { return html.value; },
    set innerHTML(value) { html.value = String(value); }
  };
  const elements = new Map();
  const document = {
    querySelector: selector => (selector === '#app' ? appRoot : (elements.get(selector) || null)),
    querySelectorAll: () => [],
    addEventListener() {},
    visibilityState: 'visible',
    documentElement: { classList: { toggle() {} }, style: { setProperty() {} } }
  };
  const stored = new Map();
  const localStorage = {
    getItem: key => stored.get(key) || null,
    setItem: (key, value) => stored.set(key, String(value)),
    removeItem: key => stored.delete(key)
  };
  const accessApi = {
    isConfigured: () => true,
    hasSession: () => hasSession,
    health: async () => ({ ok: true, status: 200 }),
    startDemo: async () => ({ ok: false, code: 'NETWORK_ERROR', message: 'offline' }),
    me: async () => ({ ok: false, code: 'NETWORK_ERROR', message: 'offline' }),
    register: async () => ({ ok: false }),
    login: async () => ({ ok: false }),
    activate: async () => ({ ok: false }),
    consume: async () => ({ ok: false, code: 'NETWORK_ERROR', message: 'offline' }),
    syncDemo: async () => ({ ok: true, imported: 0 }),
    logout: async () => ({ ok: true }),
    clearSession() {},
    ...(api || {})
  };
  const window = {
    location: {
      href: 'https://appassets.androidplatform.net/assets/index.html',
      origin: 'https://appassets.androidplatform.net',
      hostname: 'appassets.androidplatform.net',
      protocol: 'https:'
    },
    scrollTo() {},
    addEventListener() {},
    EnglishQuestAccess: accessApi
  };
  const context = {
    window, document, localStorage, navigator: {}, console,
    setTimeout: () => 1, clearTimeout() {}, Date, Math, JSON, Map, Set, Object, Array,
    String, Number, Boolean, RegExp, Intl, URL,
    Blob: class Blob {}, confirm: () => false, performance: { now: () => 0 },
    fetch: async () => { throw new Error('offline'); }
  };
  vm.createContext(context);
  for (const filename of ['library-data.js', 'mission-data.js', 'mission-data-extras.js']) {
    vm.runInContext(fs.readFileSync(path.join(ROOT, filename), 'utf8'), context, { filename });
  }
  const source = fs.readFileSync(path.join(ROOT, 'app.js'), 'utf8') +
    '\nwindow.__eqFail={state,profile,get accessState(){return accessState},' +
    'isRecoverableServerFailure,setVerifiedAccount,setLocalDemoAccess,initializeAccess,' +
    'hasAppAccess,isLocalDemo,isPremiumAccess,hasVerifiedAccess,demoCanOpenPage,' +
    'startFreeDemo,activatePremiumToken,DEMO_QUESTION_IDS,AccessApi};';
  vm.runInContext(source, context, { filename: 'app.js', timeout: 20000 });
  return { qa: window.__eqFail, root: appRoot, window, localStorage, api: accessApi };
}

const tick = () => new Promise(resolve => setImmediate(resolve));

/* -------------------------------------------------------------------- run */

(async () => {
  const results = [];
  const record = name => results.push(name);

  /* 1. A server that accepts the connection but never answers must not hang the UI. */
  const hanging = await startServer(() => { /* deliberately never responds */ });
  try {
    const { api } = loadAccessClient({ baseUrl: hanging.base, timeoutMs: 700 });
    const started = Date.now();
    const result = await withDeadline(api.health(), 8000, 'hanging server');
    const elapsed = Date.now() - started;
    assert.equal(result.ok, false, 'a non-answering server must never look healthy');
    assert.equal(result.code, 'NETWORK_TIMEOUT', `expected NETWORK_TIMEOUT, got ${result.code}`);
    assert.ok(elapsed < 6000, `timeout must fire promptly, took ${elapsed}ms`);
    assert.match(result.message, /batas waktu/i);
    record('stalled server times out instead of hanging forever');
  } finally {
    await hanging.close();
  }

  /* 2. Connection refused is a plain network error. */
  const refused = await startServer((req, res) => res.end('{}'));
  const refusedBase = refused.base;
  await refused.close();
  {
    const { api } = loadAccessClient({ baseUrl: refusedBase, timeoutMs: 2000 });
    const result = await withDeadline(api.health(), 8000, 'refused connection');
    assert.equal(result.ok, false);
    assert.equal(result.code, 'NETWORK_ERROR');
    record('refused connection reports NETWORK_ERROR (no crash, no success)');
  }

  /* 3. Happy path still works and POSTs announce JSON for the hardened backend. */
  let seenContentType = null;
  let seenBody = null;
  const healthy = await startServer((req, res) => {
    seenContentType = req.headers['content-type'] || null;
    let raw = '';
    req.on('data', chunk => { raw += chunk; });
    req.on('end', () => {
      seenBody = raw;
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(req.url === '/api/login'
        ? { ok: true, session_token: 'session-abc', session_expires_at: '2030-01-01T00:00:00Z' }
        : { ok: true, service: 'english-quest', status: 'ready' }));
    });
  });
  try {
    const { api, stored } = loadAccessClient({ baseUrl: healthy.base, timeoutMs: 4000 });
    const health = await withDeadline(api.health(), 8000, 'healthy server');
    assert.equal(health.ok, true, 'healthy server must still report ok');
    assert.equal(health.status, 200);

    const login = await api.login('learner@example.test', 'a-long-api-password-42');
    assert.equal(login.ok, true);
    assert.equal(seenContentType, 'application/json',
      'the client must send application/json or the server answers 415');
    assert.equal(JSON.parse(seenBody).email, 'learner@example.test');
    assert.equal(stored.get('englishQuest.session.v1'), 'session-abc',
      'a successful login stores the opaque session token');
    record('healthy server still works and POSTs send application/json');
  } finally {
    await healthy.close();
  }

  /* 4. 401 clears the stored session instead of believing a stale one. */
  const unauthorised = await startServer((req, res) => {
    res.writeHead(401, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: false, code: 'AUTH_REQUIRED', message: 'masuk dulu' }));
  });
  try {
    const { api, stored } = loadAccessClient({ baseUrl: unauthorised.base, timeoutMs: 4000 });
    stored.set('englishQuest.session.v1', 'stale-token');
    const result = await api.me();
    assert.equal(result.ok, false);
    assert.equal(result.status, 401);
    assert.equal(stored.has('englishQuest.session.v1'), false,
      'a 401 must drop the stored session');
    record('401 clears the stored session');
  } finally {
    await unauthorised.close();
  }

  /* 5. Garbage payloads and server errors are never treated as success. */
  const broken = await startServer((req, res) => {
    if (req.url === '/api/health') { res.writeHead(200, { 'Content-Type': 'text/html' }); res.end('<html>not json</html>'); return; }
    res.writeHead(500, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: false, code: 'SERVER_ERROR', message: 'rusak' }));
  });
  try {
    const { api } = loadAccessClient({ baseUrl: broken.base, timeoutMs: 4000 });
    const unreadable = await api.health();
    assert.equal(unreadable.ok, false);
    assert.equal(unreadable.code, 'INVALID_SERVER_RESPONSE');
    const serverError = await api.me();
    assert.equal(serverError.ok, false);
    assert.equal(serverError.status, 500);
    const activation = await api.activate('EQP-' + 'A'.repeat(48));
    assert.equal(activation.ok, false, 'a failing server must never confirm Premium');
    record('unreadable/500 responses fail closed (no Premium from a broken server)');
  } finally {
    await broken.close();
  }

  /* 6. app.js treats a timeout as recoverable and still refuses to unlock Premium. */
  {
    const harness = makeHarness({
      hasSession: true,
      api: {
        me: async () => ({ ok: false, code: 'NETWORK_TIMEOUT', message: 'server tidak merespons' }),
        health: async () => ({ ok: false, code: 'NETWORK_TIMEOUT', message: 'server tidak merespons' })
      }
    });
    const { qa } = harness;
    assert.equal(qa.isRecoverableServerFailure({ code: 'NETWORK_TIMEOUT' }), true,
      'a timeout must count as a recoverable failure so offline demo still opens');
    assert.equal(qa.isRecoverableServerFailure({ code: 'NETWORK_ERROR' }), true);
    assert.equal(qa.isRecoverableServerFailure({ code: 'AUTH_REQUIRED' }), false);

    await withDeadline(qa.initializeAccess(), 8000, 'initializeAccess');
    await tick();
    assert.equal(qa.hasVerifiedAccess(), false, 'a timed-out server is not a verified session');
    assert.equal(qa.isPremiumAccess(), false, 'a timed-out server must not unlock Premium');
    assert.equal(qa.isLocalDemo(), true, 'offline learning must fall back to the local demo');
    assert.equal(qa.hasAppAccess(), true, 'the learner must still be able to learn offline');
    assert.equal(qa.demoCanOpenPage('home'), true);
    assert.equal(qa.demoCanOpenPage('premium-access'), true);
    assert.equal(qa.demoCanOpenPage('story'), false, 'Premium content stays locked offline');
    record('timeout falls back to the limited local demo and keeps Premium locked');
  }

  /* 7. A verified premium account is still recognised (positive path unbroken). */
  {
    const harness = makeHarness({
      hasSession: true,
      api: {
        me: async () => ({
          ok: true, status: 200,
          account: {
            user_id: 'u-1', email: 'premium@example.test', role: 'user', anonymous: false,
            premium: true, premium_expires_at: '2030-01-01T00:00:00Z',
            demo_limit: 5, demo_used: 3, demo_remaining: 2, used_question_ids: []
          }
        })
      }
    });
    await withDeadline(harness.qa.initializeAccess(), 8000, 'initializeAccess (premium)');
    await tick();
    assert.equal(harness.qa.hasVerifiedAccess(), true);
    assert.equal(harness.qa.isPremiumAccess(), true, 'server-confirmed Premium must unlock');
    assert.equal(harness.qa.accessState.demoRemaining, 2);
    record('server-confirmed Premium still unlocks (no regression)');
  }

  /* 8. Local demo quota cannot be inflated by a failing server. */
  {
    const harness = makeHarness({
      hasSession: true,
      api: {
        me: async () => ({ ok: false, code: 'NETWORK_ERROR', message: 'offline' }),
        syncDemo: async () => ({ ok: false, code: 'NETWORK_ERROR', message: 'offline' })
      }
    });
    const { qa } = harness;
    qa.setLocalDemoAccess('offline', 'offline');
    const limit = qa.accessState.demoLimit;
    const remaining = qa.accessState.demoRemaining;
    assert.equal(limit, 5);
    assert.ok(remaining <= 5 && remaining >= 0, `remaining quota must stay within the limit, got ${remaining}`);
    assert.equal(qa.isPremiumAccess(), false);
    assert.equal(qa.DEMO_QUESTION_IDS.size, 5, 'the demo bank stays at five fixed question IDs');
    record('offline fallback keeps a bounded 5-question demo quota');
  }

  console.log(`Client failure paths: ${results.length} groups passed`);
  for (const name of results) console.log(`  ✓ ${name}`);
})().catch(error => {
  console.error('CLIENT FAILURE-PATH TESTS FAILED');
  console.error(error && error.stack ? error.stack : error);
  process.exit(1);
});

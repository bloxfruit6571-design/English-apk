/* Registration regression tests.
 *
 * Why this file exists: users reported that account registration did not work. The root cause was
 * in the Android shell — app.js/access-client.js asked the native bridge for the API origin
 * (window.EnglishQuestAndroid.getApiBaseUrl()), but MainActivity never exposed that method, so the
 * client fell back to the WebView asset origin, concluded "server not configured" and kept the
 * registration button disabled. These tests lock in every link of that chain:
 *
 *   1. access-client.js against real local HTTP servers (success, 400/409 rejections, refused
 *      connection, hang/timeout, unreadable payload).
 *   2. The Android bridge wiring, including the index.html flag script that reads
 *      isHttpDevAllowed() from the bridge.
 *   3. The registration form in app.js (local validation, show/hide password, post-registration
 *      login, and the "backend unavailable" path).
 *   4. End to end against the real backend/server.py on a loopback port: register with a
 *      six-character password, log in again from a fresh session, reject a wrong password, reject
 *      a duplicate account, and confirm the plaintext password never lands in the database file.
 *
 * Every credential in here is a synthetic throwaway string created for the test; no real user
 * password and no production secret is ever written to disk or printed.
 *
 * Run with: node tests/client_registration.test.js
 */
'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const vm = require('node:vm');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawn } = require('node:child_process');

const ROOT = path.resolve(__dirname, '..');
const ASSET_ORIGIN = 'https://appassets.androidplatform.net';
const THROWAWAY_PASSWORD = 'ab12cd';       // exactly six characters: the documented minimum
const TOO_SHORT_PASSWORD = 'ab12c';        // five characters: must be rejected
const results = [];
const record = (name) => results.push(name);

/* ------------------------------------------------------------------ helpers */

function startServer(handler) {
  const sockets = new Set();
  const server = http.createServer(handler);
  server.on('connection', (socket) => {
    sockets.add(socket);
    socket.on('close', () => sockets.delete(socket));
  });
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => {
      resolve({
        server,
        base: `http://127.0.0.1:${server.address().port}`,
        async close() {
          for (const socket of sockets) socket.destroy();
          await new Promise((done) => server.close(done));
        }
      });
    });
  });
}

function readJsonBody(request) {
  return new Promise((resolve) => {
    let raw = '';
    request.on('data', (chunk) => { raw += chunk; });
    request.on('end', () => { try { resolve(JSON.parse(raw || '{}')); } catch (_) { resolve({}); } });
  });
}

const EMAIL_RE = /^[^\s@]{1,64}@[^\s@.]{1,128}(?:\.[^\s@.]{1,63})+$/;

/** A miniature stand-in for the account API, mirroring backend/server.py's contract. */
function fakeBackend({ onRegister } = {}) {
  const users = new Map();
  const sessions = new Map();
  let nextToken = 0;
  const handler = async (request, response) => {
    const url = new URL(request.url, 'http://127.0.0.1');
    const send = (status, payload) => {
      response.writeHead(status, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify(payload));
    };
    const body = request.method === 'POST' ? await readJsonBody(request) : {};
    if (url.pathname === '/api/register' && request.method === 'POST') {
      if (onRegister) onRegister(body);
      const email = String(body.email || '').trim().toLowerCase();
      const password = String(body.password || '');
      if (!EMAIL_RE.test(email)) {
        return send(400, { ok: false, code: 'INVALID_EMAIL', message: 'Masukkan alamat email yang valid.' });
      }
      if (password.length < 6 || password.length > 256) {
        return send(400, { ok: false, code: 'WEAK_PASSWORD', message: 'Kata sandi harus 6–256 karakter.' });
      }
      if (users.has(email)) {
        return send(409, { ok: false, code: 'ACCOUNT_EXISTS', message: 'Akun dengan email tersebut sudah ada. Silakan masuk.' });
      }
      users.set(email, password);
      const token = `session-${++nextToken}`;
      sessions.set(token, email);
      return send(200, { ok: true, session_token: token, session_expires_at: '2030-01-01T00:00:00Z' });
    }
    if (url.pathname === '/api/login' && request.method === 'POST') {
      const email = String(body.email || '').trim().toLowerCase();
      const password = String(body.password || '');
      if (!users.has(email) || users.get(email) !== password) {
        return send(401, { ok: false, code: 'INVALID_CREDENTIALS', message: 'Email atau kata sandi salah.' });
      }
      const token = `session-${++nextToken}`;
      sessions.set(token, email);
      return send(200, { ok: true, session_token: token, session_expires_at: '2030-01-01T00:00:00Z' });
    }
    if (url.pathname === '/api/me') {
      const token = String(request.headers.authorization || '').replace(/^Bearer\s+/i, '');
      const email = sessions.get(token);
      if (!email) return send(401, { ok: false, code: 'AUTH_REQUIRED', message: 'Sesi tidak valid.' });
      return send(200, {
        ok: true,
        account: {
          user_id: 'user-1', email, role: 'user', anonymous: false, premium: false,
          demo_limit: 5, demo_used: 0, demo_remaining: 5, used_question_ids: []
        }
      });
    }
    if (url.pathname === '/api/health') return send(200, { ok: true });
    return send(404, { ok: false, code: 'NOT_FOUND', message: 'Tidak ditemukan.' });
  };
  return { users, handler };
}

/** The inline flag script from index.html, so tests exercise the real wiring, not a copy. */
function indexFlagScript() {
  const html = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');
  const match = html.match(/<script>([\s\S]*?)<\/script>/);
  assert.ok(match, 'index.html must keep its inline flag script');
  return match[1];
}

/**
 * Load the real access-client.js. By default the page runs at the WebView asset origin, exactly
 * like inside the APK, so the only way to reach a server is the native bridge.
 */
function loadAccessClient({ bridge, locationHref = `${ASSET_ORIGIN}/assets/index.html`, useIndexFlags = true } = {}) {
  const stored = new Map();
  const localStorage = {
    getItem: (key) => (stored.has(key) ? stored.get(key) : null),
    setItem: (key, value) => stored.set(key, String(value)),
    removeItem: (key) => stored.delete(key)
  };
  const url = new URL(locationHref);
  const window = {
    location: { href: url.href, origin: url.origin, hostname: url.hostname, protocol: url.protocol }
  };
  if (bridge) window.EnglishQuestAndroid = bridge;
  const context = {
    window, localStorage, console, fetch, AbortController, URL,
    // Browsers expose window.location as a bare global; index.html's inline script relies on it.
    location: window.location,
    setTimeout, clearTimeout, clearInterval, String, Number, Boolean, Object, Array,
    JSON, Error, Promise, Map, Set
  };
  context.globalThis = context;
  vm.createContext(context);
  if (useIndexFlags) vm.runInContext(indexFlagScript(), context, { filename: 'index-inline-flags.js' });
  vm.runInContext(fs.readFileSync(path.join(ROOT, 'access-client.js'), 'utf8'), context,
    { filename: 'access-client.js' });
  return { api: window.EnglishQuestAccess, window, stored };
}

/** app.js harness in the same DOM-stub style as tests/client_gates.test.js. */
function makeHarness({ api } = {}) {
  const html = { value: '' };
  const appRoot = {
    addEventListener() {},
    get innerHTML() { return html.value; },
    set innerHTML(value) { html.value = String(value); }
  };
  const elements = new Map();
  const document = {
    querySelector: (selector) => (selector === '#app' ? appRoot : (elements.get(selector) || null)),
    querySelectorAll: () => [],
    addEventListener() {},
    visibilityState: 'visible',
    documentElement: { classList: { toggle() {} }, style: { setProperty() {} } }
  };
  const stored = new Map();
  const localStorage = {
    getItem: (key) => stored.get(key) || null,
    setItem: (key, value) => stored.set(key, String(value)),
    removeItem: (key) => stored.delete(key)
  };
  const calls = { register: 0, login: 0 };
  const overrides = api || {};
  // A successful register/login is what makes AccessApi.hasSession() true, exactly like the real
  // client, so submitAccessAuth() can finish its post-registration verification step.
  let session = false;
  const accessApi = {
    isConfigured: () => true,
    hasSession: () => session,
    health: async () => ({ ok: true, status: 200 }),
    startDemo: async () => ({ ok: false, code: 'NETWORK_ERROR', message: 'offline' }),
    me: async () => ({ ok: false, code: 'AUTH_REQUIRED', message: 'not logged in' }),
    activate: async () => ({ ok: false }),
    consume: async () => ({ ok: false, code: 'NETWORK_ERROR', message: 'offline' }),
    syncDemo: async () => ({ ok: true, imported: 0 }),
    logout: async () => ({ ok: true }),
    clearSession() {},
    ...overrides,
    register: async (email, password) => {
      calls.register += 1;
      const result = overrides.register ? await overrides.register(email, password) : { ok: false };
      if (result && result.ok) session = true;
      return result;
    },
    login: async (email, password) => {
      calls.login += 1;
      const result = overrides.login ? await overrides.login(email, password) : { ok: false };
      if (result && result.ok) session = true;
      return result;
    }
  };
  const window = {
    location: { href: `${ASSET_ORIGIN}/assets/index.html`, origin: ASSET_ORIGIN, hostname: 'appassets.androidplatform.net', protocol: 'https:' },
    scrollTo() {}, addEventListener() {}, EnglishQuestAccess: accessApi
  };
  const context = {
    window, document, localStorage, navigator: {}, console: { warn() {}, error() {}, log() {} },
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
    '\nwindow.__eqQA={state,get accessState(){return accessState},setVerifiedAccount,submitAccessAuth,' +
    'togglePasswordVisibility,PASSWORD_MIN_LENGTH,isPremiumAccess,render,AccessApi};';
  vm.runInContext(source, context, { filename: 'app.js', timeout: 15000 });
  return { qa: window.__eqQA, root: appRoot, elements, localStorage, calls };
}

function withDeadline(promise, ms, label) {
  return Promise.race([
    promise,
    new Promise((_, reject) => setTimeout(
      () => reject(new Error(`${label}: timed out after ${ms}ms — the client hung`)), ms))
  ]);
}

async function waitForHealth(base, budgetMs = 15000) {
  const started = Date.now();
  while (Date.now() - started < budgetMs) {
    try {
      const response = await fetch(`${base}/api/health`);
      if (response.ok) return true;
    } catch (_) { /* not up yet */ }
    await new Promise((done) => setTimeout(done, 120));
  }
  throw new Error('backend did not become healthy in time');
}

/* --------------------------------------------------------------------- run */

(async () => {
  const backend = fakeBackend();
  const live = await startServer(backend.handler);
  try {
    /* 1. Registration succeeds and the session is remembered for later calls. */
    {
      const client = loadAccessClient({
        bridge: { getApiBaseUrl: () => live.base, isHttpDevAllowed: () => true }
      });
      const email = 'fresh-learner@example.test';
      const registered = await withDeadline(client.api.register(email, THROWAWAY_PASSWORD), 5000, 'register');
      assert.equal(registered.ok, true, 'registration must succeed against a working server');
      assert.ok(client.stored.get('englishQuest.session.v1'), 'the session token must be stored');
      const me = await client.api.me();
      assert.equal(me.ok, true);
      assert.equal(me.account.email, email);
      assert.equal(me.account.premium, false, 'a new account never starts Premium');
      record('registration succeeds, stores the session and returns a non-premium account');
    }

    /* 2. A password shorter than six characters is rejected by the server. */
    {
      const client = loadAccessClient({
        bridge: { getApiBaseUrl: () => live.base, isHttpDevAllowed: () => true }
      });
      const refused = await client.api.register('too-short@example.test', TOO_SHORT_PASSWORD);
      assert.equal(refused.ok, false);
      assert.equal(refused.code, 'WEAK_PASSWORD');
      assert.match(refused.message, /6.{0,6}256 karakter|minimal 6/i, 'the message must state the minimum');
      assert.ok(!client.stored.get('englishQuest.session.v1'), 'a rejected attempt must not create a session');
      record('five-character password is rejected with a readable WEAK_PASSWORD message');
    }

    /* 3. An invalid email is rejected. */
    {
      const client = loadAccessClient({
        bridge: { getApiBaseUrl: () => live.base, isHttpDevAllowed: () => true }
      });
      const refused = await client.api.register('not-an-email', THROWAWAY_PASSWORD);
      assert.equal(refused.ok, false);
      assert.equal(refused.code, 'INVALID_EMAIL');
      record('invalid email is rejected with INVALID_EMAIL');
    }

    /* 4. A duplicate account surfaces the server's explanation instead of a generic error. */
    {
      const client = loadAccessClient({
        bridge: { getApiBaseUrl: () => live.base, isHttpDevAllowed: () => true }
      });
      const again = await client.api.register('fresh-learner@example.test', THROWAWAY_PASSWORD);
      assert.equal(again.ok, false);
      assert.equal(again.code, 'ACCOUNT_EXISTS');
      assert.match(again.message, /sudah ada/i);
      record('registering an existing email is reported as ACCOUNT_EXISTS');
    }

    /* 5. Backend unavailable: connection refused. */
    {
      const dead = await startServer(backend.handler);
      const deadBase = dead.base;
      await dead.close();
      const client = loadAccessClient({ bridge: { getApiBaseUrl: () => deadBase, isHttpDevAllowed: () => true } });
      const refused = await client.api.register('offline@example.test', THROWAWAY_PASSWORD);
      assert.equal(refused.ok, false);
      assert.equal(refused.code, 'NETWORK_ERROR');
      assert.match(refused.message, /Tidak dapat terhubung/i);
      record('backend unavailable reports NETWORK_ERROR without unlocking anything');
    }

    /* 6. Server accepts the connection but never answers: bounded timeout, no hang. */
    {
      const hanging = await startServer(() => { /* deliberately silent */ });
      const client = loadAccessClient({
        bridge: { getApiBaseUrl: () => hanging.base, isHttpDevAllowed: () => true }
      });
      client.window.EQ_REQUEST_TIMEOUT_MS = 250;
      const started = Date.now();
      const refused = await withDeadline(client.api.register('slow@example.test', THROWAWAY_PASSWORD), 5000, 'timeout path');
      assert.equal(refused.code, 'NETWORK_TIMEOUT');
      assert.ok(Date.now() - started < 4000, 'the client must give up on its own');
      await hanging.close();
      record('an unresponsive server ends in NETWORK_TIMEOUT instead of freezing the form');
    }

    /* 7. Unreadable server response. */
    {
      const garbage = await startServer((request, response) => {
        response.writeHead(200, { 'Content-Type': 'text/html' });
        response.end('<html>proxy error</html>');
      });
      const client = loadAccessClient({
        bridge: { getApiBaseUrl: () => garbage.base, isHttpDevAllowed: () => true }
      });
      const refused = await client.api.register('garbled@example.test', THROWAWAY_PASSWORD);
      assert.equal(refused.ok, false);
      assert.equal(refused.code, 'INVALID_SERVER_RESPONSE');
      await garbage.close();
      record('an unreadable server response reports INVALID_SERVER_RESPONSE');
    }

    /* 8. The Android bridge is what makes registration possible at all. */
    {
      const withoutBridge = loadAccessClient({ locationHref: `${ASSET_ORIGIN}/assets/index.html` });
      assert.equal(withoutBridge.api.isConfigured(), false,
        'with no bridge method the asset origin is not a valid API base (the original APK failure)');
      const refused = await withoutBridge.api.register('nobridge@example.test', THROWAWAY_PASSWORD);
      assert.equal(refused.code, 'API_NOT_CONFIGURED');
      assert.ok(withoutBridge.window.EQ_ALLOW_HTTP_DEV === false, 'http dev stays off without the bridge flag');

      const withBridge = loadAccessClient({
        bridge: {
          getApiBaseUrl: () => live.base,
          isHttpDevAllowed: () => true
        }
      });
      assert.equal(withBridge.api.isConfigured(), true, 'the bridge origin must be accepted');
      assert.equal(withBridge.api.getApiBaseUrl(), live.base);
      assert.equal(withBridge.window.EQ_ALLOW_HTTP_DEV, true, 'index.html reads isHttpDevAllowed() from the bridge');
      const registered = await withBridge.api.register('bridged@example.test', THROWAWAY_PASSWORD);
      assert.equal(registered.ok, true);
      assert.equal(registered.status, 200);
      record('the native bridge supplies the API origin; without it the form stays unconfigured');
    }

    /* 9. Form-level validation and the show/hide password control. */
    {
      const harness = makeHarness({ api: { me: async () => ({ ok: false }) } });
      const { qa, elements, calls } = harness;
      qa.state.accessFlow = 'demo';
      qa.state.authTab = 'register';

      elements.set('#access-email', { value: 'short@example.test' });
      elements.set('#access-password', { value: TOO_SHORT_PASSWORD });
      await qa.submitAccessAuth();
      assert.equal(calls.register, 0, 'a too-short password must not reach the network');
      assert.match(qa.state.authError, /minimal 6 karakter/i);
      assert.equal(qa.state.page !== 'home', true);

      elements.set('#access-email', { value: 'not-an-email' });
      elements.set('#access-password', { value: 'good-one' });
      await qa.submitAccessAuth();
      assert.equal(calls.register, 0, 'an invalid email must not reach the network');
      assert.match(qa.state.authError, /email belum valid/i);

      const passwordInput = { type: 'password', value: 'rahasia-uji' };
      const toggleButton = {
        textContent: '👁️ Lihat',
        setAttribute(name, value) { this[name] = value; }
      };
      elements.set('#access-password', passwordInput);
      elements.set('[data-action="toggle-password-visibility"]', toggleButton);
      const revealed = qa.togglePasswordVisibility();
      assert.equal(revealed, true);
      assert.equal(passwordInput.type, 'text', 'the field must show the password');
      assert.equal(passwordInput.value, 'rahasia-uji', 'toggling must not clear what was typed');
      assert.equal(toggleButton['aria-pressed'], 'true');
      assert.match(toggleButton.textContent, /Sembunyikan/);
      const hiddenAgain = qa.togglePasswordVisibility();
      assert.equal(hiddenAgain, false);
      assert.equal(passwordInput.type, 'password', 'the field must hide the password again');
      assert.equal(passwordInput.value, 'rahasia-uji');
      assert.equal(toggleButton['aria-pressed'], 'false');
      record('local validation blocks bad input and the show/hide password control preserves typing');
    }

    /* 10. Successful registration through the real form, then log in afterwards. */
    {
      const account = {
        user_id: 'user-2', email: 'form-learner@example.test', role: 'user', anonymous: false,
        premium: false, demo_limit: 5, demo_used: 0, demo_remaining: 5, used_question_ids: []
      };
      const harness = makeHarness({
        api: {
          register: async () => ({ ok: true, session_token: 'tok' }),
          me: async () => ({ ok: true, account })
        }
      });
      const { qa, elements, calls } = harness;
      qa.state.authTab = 'register';
      elements.set('#access-email', { value: account.email });
      elements.set('#access-password', { value: THROWAWAY_PASSWORD });
      await qa.submitAccessAuth();
      assert.equal(calls.register, 1, 'the register call happens exactly once');
      assert.equal(qa.state.authError, '');
      assert.equal(qa.accessState.status, 'verified');
      assert.equal(qa.accessState.user.email, account.email);
      assert.equal(qa.state.page, 'home');

      const loginHarness = makeHarness({
        api: {
          login: async () => ({ ok: true, session_token: 'tok2' }),
          me: async () => ({ ok: true, account: { ...account, demo_used: 2, demo_remaining: 3 } })
        }
      });
      loginHarness.qa.state.authTab = 'login';
      loginHarness.elements.set('#access-email', { value: account.email });
      loginHarness.elements.set('#access-password', { value: THROWAWAY_PASSWORD });
      await loginHarness.qa.submitAccessAuth();
      assert.equal(loginHarness.calls.login, 1);
      assert.equal(loginHarness.qa.accessState.demoRemaining, 3);
      assert.equal(loginHarness.qa.state.page, 'home');
      record('form registration verifies the account and the same credentials can log in later');
    }

    /* 11. Backend unavailable while registering shows the reason and keeps the user on the form. */
    {
      const harness = makeHarness({
        api: {
          register: async () => ({ ok: false, code: 'NETWORK_ERROR', message: 'Tidak dapat terhubung ke server. Periksa internet, lalu coba lagi.' })
        }
      });
      const { qa, elements } = harness;
      qa.state.authTab = 'register';
      elements.set('#access-email', { value: 'offline-form@example.test' });
      elements.set('#access-password', { value: THROWAWAY_PASSWORD });
      await qa.submitAccessAuth();
      assert.equal(qa.state.authError, 'Tidak dapat terhubung ke server. Periksa internet, lalu coba lagi.');
      assert.equal(qa.state.page === 'home', false, 'a failed registration must not reach the home page');
      assert.notEqual(qa.accessState.status, 'verified');
      assert.equal(qa.isPremiumAccess(), false, 'a failed registration never grants Premium');
      record('a failing backend leaves the user on the form with the server message shown');
    }

    /* 12. End to end against the real backend: database, hashing and login after registration. */
    {
      const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'eq-register-'));
      const dbPath = path.join(temp, 'accounts.sqlite3');
      const child = spawn(process.env.PYTHON || 'python3', [path.join(ROOT, 'backend', 'server.py')], {
        env: {
          ...process.env,
          PYTHONPATH: ROOT,
          EQ_ALLOW_INSECURE_DEV: '1',
          EQ_HOST: '127.0.0.1',
          EQ_PORT: '0',
          EQ_DB_PATH: dbPath,
          // Throwaway pepper for this throwaway database only; never a production secret.
          EQ_TOKEN_PEPPER: crypto.randomBytes(32).toString('hex')
        },
        stdio: ['ignore', 'pipe', 'pipe']
      });
      let stdout = '';
      let stderr = '';
      child.stdout.on('data', (chunk) => { stdout += chunk; });
      child.stderr.on('data', (chunk) => { stderr += chunk; });
      try {
        const base = await withDeadline(new Promise((resolve, reject) => {
          const timer = setInterval(() => {
            const match = stdout.match(/listening on ([0-9.]+):(\d+)/i);
            if (match) { clearInterval(timer); resolve(`http://${match[1]}:${match[2]}`); }
          }, 100);
          child.once('exit', (code) => { clearInterval(timer); reject(new Error(`backend exited early with code ${code}: ${stderr}`)); });
        }), 15000, 'backend start');
        await waitForHealth(base);

        const email = 'end-to-end@example.test';
        const bridge = { getApiBaseUrl: () => base, isHttpDevAllowed: () => true };
        const first = loadAccessClient({ bridge });
        const created = await withDeadline(first.api.register(email, THROWAWAY_PASSWORD), 10000, 'real register');
        assert.equal(created.ok, true, 'the real backend must accept a six-character password');
        const status = await first.api.me();
        assert.equal(status.ok, true);
        assert.equal(status.account.email, email);
        assert.equal(status.account.premium, false);
        assert.equal(status.account.demo_remaining, 5);

        const shortRefused = await first.api.register('too-short-real@example.test', TOO_SHORT_PASSWORD);
        assert.equal(shortRefused.ok, false);
        assert.equal(shortRefused.code, 'WEAK_PASSWORD');

        const duplicate = await first.api.register(email, THROWAWAY_PASSWORD);
        assert.equal(duplicate.code, 'ACCOUNT_EXISTS');

        const second = loadAccessClient({ bridge });   // fresh session, like a reinstall
        const loggedIn = await withDeadline(second.api.login(email, THROWAWAY_PASSWORD), 10000, 'real login');
        assert.equal(loggedIn.ok, true, 'the account created moments earlier must be able to log in');
        const afterLogin = await second.api.me();
        assert.equal(afterLogin.ok, true);
        assert.equal(afterLogin.account.email, email);

        const wrong = loadAccessClient({ bridge });
        const refused = await wrong.api.login(email, 'wrong-password');
        assert.equal(refused.ok, false);
        assert.equal(refused.code, 'INVALID_CREDENTIALS');
        assert.ok(!wrong.stored.get('englishQuest.session.v1'));

        const dbBytes = fs.readFileSync(dbPath);
        assert.equal(dbBytes.includes(Buffer.from(THROWAWAY_PASSWORD)), false,
          'the plaintext password must never appear in the database file');
        assert.equal(dbBytes.includes(Buffer.from(email)), true, 'the account row itself must exist');
        record('end to end: real backend registers, hashes, stores, then logs the user back in');
      } finally {
        child.kill('SIGTERM');
        await new Promise((done) => { child.once('exit', done); setTimeout(done, 3000); });
        fs.rmSync(temp, { recursive: true, force: true });
      }
    }

    console.log(`Client registration: ${results.length} groups passed`);
    for (const name of results) console.log(`  ✓ ${name}`);
  } finally {
    await live.close();
  }
})().catch((error) => {
  console.error('CLIENT REGISTRATION TESTS FAILED');
  console.error(error && error.stack ? error.stack : error);
  process.exit(1);
});

/* English Quest access client. Stores only an opaque session token; entitlement and demo usage come from the server. */
'use strict';
(() => {
  const SESSION_KEY = 'englishQuest.session.v1';
  const nativeBridge = () => { try { return window.EnglishQuestAndroid; } catch (_) { return null; } };

  function apiBase() {
    let configured = '';
    try { configured = String(window.EQ_API_BASE || '').trim(); } catch (_) {}
    if (!configured) {
      const bridge = nativeBridge();
      try { if (bridge && typeof bridge.getApiBaseUrl === 'function') configured = String(bridge.getApiBaseUrl() || '').trim(); } catch (_) {}
    }
    if (configured) {
      try {
        const url = new URL(configured);
        const localDev = ['localhost', '127.0.0.1', '::1'].includes(url.hostname);
        if (url.protocol !== 'https:' && !(url.protocol === 'http:' && localDev && window.EQ_ALLOW_HTTP_DEV === true)) return '';
        return url.href.replace(/\/+$/, '').replace(/\/api$/i, '');
      } catch (_) { return ''; }
    }
    try {
      const url = new URL(window.location.href);
      if (url.protocol === 'https:' && url.hostname !== 'appassets.androidplatform.net') return url.origin;
      if (url.protocol === 'http:' && ['localhost', '127.0.0.1', '::1'].includes(url.hostname) && window.EQ_ALLOW_HTTP_DEV === true) return url.origin;
    } catch (_) {}
    return '';
  }

  function getSession() {
    try { return String(localStorage.getItem(SESSION_KEY) || ''); } catch (_) { return ''; }
  }
  function setSession(token) {
    try { if (token) localStorage.setItem(SESSION_KEY, token); else localStorage.removeItem(SESSION_KEY); return true; }
    catch (_) { return false; }
  }

  const DEFAULT_TIMEOUT_MS = 15000;
  function requestTimeoutMs() {
    try {
      const configured = Number(window.EQ_REQUEST_TIMEOUT_MS);
      if (Number.isFinite(configured) && configured > 0) return configured;
    } catch (_) {}
    return DEFAULT_TIMEOUT_MS;
  }

  async function request(path, options = {}, authenticated = true) {
    const base = apiBase();
    if (!base) return { ok: false, code: 'API_NOT_CONFIGURED', message: 'Server akun belum dikonfigurasi. Akses demo dan Premium perlu diverifikasi secara online.' };
    const headers = { 'Accept': 'application/json', ...(options.headers || {}) };
    if (options.body !== undefined) headers['Content-Type'] = 'application/json';
    const token = getSession();
    if (authenticated && token) headers.Authorization = `Bearer ${token}`;
    // A server that accepts the connection but never answers must not freeze the app: without an
    // abort the awaiting UI (for example "Memeriksa akun…") would wait forever. Old WebViews
    // without AbortController keep the previous behaviour instead of failing.
    let timer = null;
    let controller = null;
    if (typeof AbortController === 'function') {
      controller = new AbortController();
      timer = setTimeout(() => { try { controller.abort(); } catch (_) {} }, requestTimeoutMs());
    }
    try {
      const response = await fetch(`${base}/api${path}`, {
        ...options, headers, credentials: 'omit', cache: 'no-store', redirect: 'error',
        ...(controller ? { signal: controller.signal } : {})
      });
      let payload;
      try { payload = await response.json(); } catch (_) { payload = null; }
      if (!payload || typeof payload !== 'object') {
        return { ok: false, code: 'INVALID_SERVER_RESPONSE', message: 'Respons server tidak dapat dibaca. Coba lagi saat koneksi stabil.' };
      }
      if (response.status === 401 && authenticated) setSession('');
      return { ...payload, ok: Boolean(payload.ok && response.ok), status: response.status };
    } catch (_) {
      if (controller && controller.signal.aborted) {
        return { ok: false, code: 'NETWORK_TIMEOUT', message: 'Server tidak merespons dalam batas waktu. Periksa koneksi internet, lalu coba lagi.' };
      }
      return { ok: false, code: 'NETWORK_ERROR', message: 'Tidak dapat terhubung ke server. Periksa internet, lalu coba lagi.' };
    } finally {
      if (timer !== null) clearTimeout(timer);
    }
  }

  async function register(email, password) {
    const result = await request('/register', { method: 'POST', body: JSON.stringify({ email, password }) }, false);
    if (result.ok && result.session_token) {
      if (!setSession(result.session_token)) return { ok: false, code: 'SESSION_STORAGE_FAILED', message: 'Sesi tidak dapat disimpan di perangkat. Periksa ruang penyimpanan.' };
    }
    return result;
  }
  async function login(email, password) {
    const result = await request('/login', { method: 'POST', body: JSON.stringify({ email, password }) }, false);
    if (result.ok && result.session_token) {
      if (!setSession(result.session_token)) return { ok: false, code: 'SESSION_STORAGE_FAILED', message: 'Sesi tidak dapat disimpan di perangkat. Periksa ruang penyimpanan.' };
    }
    return result;
  }
  async function health() { return request('/health', { method: 'GET' }, false); }
  async function startDemo() {
    const result = await request('/demo/start', { method: 'POST', body: '{}' }, false);
    if (result.ok && result.session_token) {
      if (!setSession(result.session_token)) return { ok: false, code: 'SESSION_STORAGE_FAILED', message: 'Sesi demo tidak dapat disimpan di perangkat. Periksa ruang penyimpanan.' };
    }
    return result;
  }
  async function syncDemo(questionIds) { return request('/demo/sync', { method: 'POST', body: JSON.stringify({ question_ids: questionIds }) }, true); }
  async function me() { return request('/me', { method: 'GET' }, true); }
  async function activate(token) { return request('/premium/activate', { method: 'POST', body: JSON.stringify({ token }) }, true); }
  async function consume(questionId, answer) { return request('/demo/consume', { method: 'POST', body: JSON.stringify({ question_id: questionId, answer }) }, true); }
  async function logout() {
    const result = await request('/logout', { method: 'POST', body: '{}' }, true);
    setSession('');
    return result;
  }
  window.EnglishQuestAccess = {
    isConfigured: () => Boolean(apiBase()),
    getApiBaseUrl: () => apiBase(),
    hasSession: () => Boolean(getSession()),
    register, login, health, startDemo, syncDemo, me, activate, consume, logout,
    clearSession: () => setSession('')
  };
})();

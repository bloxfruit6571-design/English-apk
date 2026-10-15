/* Reproduksi alur pendaftaran dengan kode klien ASLI (access-client.js) terhadap backend nyata.
 *
 * Kasus:
 *  A. Build yang sudah diperbaiki (bridge getApiBaseUrl + isHttpDevAllowed) → daftar, me(), login.
 *  B. APK yang beredar di repo (tanpa metode bridge) → inilah yang dialami pengguna.
 *  C. Backend tidak berjalan → pesan galat yang diterima pengguna.
 *
 * Kata sandi di sini sintetis dan tidak pernah dicetak.
 */
'use strict';
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..');
const ASSET_ORIGIN = 'https://appassets.androidplatform.net';
const BASE = process.argv[2] || 'http://127.0.0.1:8088';
const PASSWORD = 'ab12cd';       // sintetis, 6 karakter
const EMAIL = `uji-${require('node:crypto').randomBytes(6).toString('hex')}@example.test`;
if (!BASE.startsWith('https://127.0.0.1:')) throw new Error('Hanya fixture HTTPS lokal; jangan membuat akun di server publik tanpa izin.');

function indexFlagScript() {
  const html = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');
  return /<script>([\s\S]*?)<\/script>/.exec(html)[1];
}

function loadClient(bridge) {
  const store = new Map();
  const localStorage = {
    getItem: k => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: k => store.delete(k)
  };
  const url = new URL(`${ASSET_ORIGIN}/assets/index.html`);
  const window = { location: { href: url.href, origin: url.origin, hostname: url.hostname, protocol: url.protocol } };
  if (bridge) window.EnglishQuestAndroid = bridge;
  const ctx = {
    window, localStorage, console, fetch, AbortController, URL, location: window.location,
    setTimeout, clearTimeout, clearInterval, String, Number, Boolean, Object, Array, JSON, Error, Promise, Map, Set
  };
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  vm.runInContext(indexFlagScript(), ctx, { filename: 'index-inline-flags.js' });
  vm.runInContext(fs.readFileSync(path.join(ROOT, 'access-client.js'), 'utf8'), ctx, { filename: 'access-client.js' });
  return { api: window.EnglishQuestAccess, window, store };
}

(async () => {
  let failures = 0;
  const check = (label, ok, detail) => {
    console.log(`  ${ok ? 'OK  ' : 'GAGAL'} ${label}${detail ? ' → ' + detail : ''}`);
    if (!ok) failures++;
  };

  console.log(`\n== A. Build yang sudah diperbaiki (bridge ada) → ${BASE} ==`);
  const fixed = loadClient({ getApiBaseUrl: () => BASE, isHttpDevAllowed: () => false });
  check('isConfigured()', fixed.api.isConfigured() === true, String(fixed.api.isConfigured()));
  check('Mode HTTP dev nonaktif', fixed.window.EQ_ALLOW_HTTP_DEV === false, String(fixed.window.EQ_ALLOW_HTTP_DEV));
  const reg = await fixed.api.register(EMAIL, PASSWORD);
  check('register() sukses', reg.ok === true, `status=${reg.status} code=${reg.code || '-'}`);
  check('sesi tersimpan', Boolean(fixed.store.get('englishQuest.session.v1')));
  const me = await fixed.api.me();
  check('me() mengembalikan akun', me.ok === true && me.account && me.account.email === EMAIL,
    me.ok ? `email=${me.account.email} premium=${me.account.premium} sisa=${me.account.demo_remaining}` : me.code);
  const login = await fixed.api.login(EMAIL, PASSWORD);
  check('login ulang', login.ok === true, `status=${login.status}`);

  console.log('\n== B. APK yang beredar di repo (tanpa bridge getApiBaseUrl) ==');
  const shipped = loadClient(null);
  check('isConfigured() = false (tombol Buat Akun nonaktif)', shipped.api.isConfigured() === false, String(shipped.api.isConfigured()));
  const blocked = await shipped.api.register(EMAIL, PASSWORD);
  check('register() ditolak sebelum menyentuh jaringan', blocked.ok === false && blocked.code === 'API_NOT_CONFIGURED', blocked.code);
  console.log(`       pesan yang dilihat pengguna: "${blocked.message}"`);

  console.log('\n== C. Backend tidak berjalan (port tertutup) ==');
  const dead = loadClient({ getApiBaseUrl: () => 'https://127.0.0.1:9', isHttpDevAllowed: () => false });
  const offline = await dead.api.register('offline@example.test', PASSWORD);
  check('register() melaporkan kegagalan jaringan', offline.ok === false && offline.code === 'NETWORK_ERROR', offline.code);
  console.log(`       pesan yang dilihat pengguna: "${offline.message}"`);

  console.log(failures === 0 ? '\nREPRODUKSI: semua pemeriksaan sesuai harapan' : `\nREPRODUKSI: ${failures} pemeriksaan tidak sesuai`);
  process.exit(failures === 0 ? 0 : 1);
})().catch(err => { console.error('SKRIP REPRO GAGAL:', err && err.stack ? err.stack : err); process.exit(1); });

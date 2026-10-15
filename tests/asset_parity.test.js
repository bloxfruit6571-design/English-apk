/* Asset parity guard.
 *
 * The README claims the Android build embeds the same web assets byte-for-byte as the
 * repository root ("17 asset source cocok byte-for-byte"). Nothing enforced that, so a
 * root-only fix could silently ship a stale WebView build. This test fails loudly when the
 * two copies drift apart.
 *
 * Run with: node tests/asset_parity.test.js
 */
'use strict';

const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..');
const ASSETS = path.join(ROOT, 'english-quest-android/app/src/main/assets');

const PAIRS = [
  'app.js', 'access-client.js', 'sw.js', 'styles.css', 'index.html',
  'library-data.js', 'mission-data.js', 'mission-data-extras.js',
  'manifest.webmanifest', 'icon.svg', 'icon-192.png', 'icon-512.png',
  'images/aki.webp', 'images/bimo.webp', 'images/naya.webp',
  'images/rina.webp', 'images/suki.webp'
];

const digest = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');

let checked = 0;
const problems = [];
for (const relative of PAIRS) {
  const rootFile = path.join(ROOT, relative);
  const assetFile = path.join(ASSETS, relative);
  if (!fs.existsSync(rootFile)) { problems.push(`missing in root: ${relative}`); continue; }
  if (!fs.existsSync(assetFile)) { problems.push(`missing in android assets: ${relative}`); continue; }
  const rootHash = digest(rootFile);
  if (rootHash !== digest(assetFile)) {
    problems.push(`out of sync: ${relative} (root ${rootHash.slice(0, 12)} != assets ${digest(assetFile).slice(0, 12)})`);
  }
  checked++;
}

assert.equal(problems.length, 0,
  'root web assets and english-quest-android assets must stay byte-identical:\n  ' + problems.join('\n  '));

// The service worker cache name must change whenever a cached file changes, otherwise an
// installed PWA keeps serving the old app.js from its cache-first fetch handler.
const sw = fs.readFileSync(path.join(ROOT, 'sw.js'), 'utf8');
const cacheName = /CACHE_NAME\s*=\s*'([^']+)'/.exec(sw);
assert.ok(cacheName, 'sw.js must define CACHE_NAME');
const version = Number(/v(\d+)$/.exec(cacheName[1])?.[1]);
assert.ok(Number.isFinite(version) && version >= 16,
  `sw.js cache name must be bumped past v15 after the client changes, found ${cacheName[1]}`);

// The Android asset copy must carry the same cache name, so both builds invalidate together.
const assetSw = fs.readFileSync(path.join(ASSETS, 'sw.js'), 'utf8');
assert.equal(/CACHE_NAME\s*=\s*'([^']+)'/.exec(assetSw)?.[1], cacheName[1],
  'the Android copy of sw.js must use the same cache version');

console.log(`Asset parity: ${checked} files identical between root and english-quest-android assets (cache ${cacheName[1]})`);

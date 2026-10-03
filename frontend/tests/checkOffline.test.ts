import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { findRemoteLoads } from '../scripts/check-offline.mjs';

function dist(files: Record<string, string>): string {
  const dir = mkdtempSync(join(tmpdir(), 'offline-'));
  mkdirSync(join(dir, 'assets'));
  for (const [name, body] of Object.entries(files)) writeFileSync(join(dir, name), body);
  return dir;
}

test('local bundle passes, documentation URLs in strings are fine', () => {
  const dir = dist({ 'index.html': '<script type="module" src="/assets/a.js"></script>',
    'assets/a.js': 'throw Error("see https://react.dev/errors/1")', 'assets/a.css': 'a{background:url(/x.png)}' });
  assert.deepEqual(findRemoteLoads(dir), []);
});

test('remote script, stylesheet, font and dynamic import are caught', () => {
  const dir = dist({
    'index.html': '<script src="https://cdn.example/x.js"></script><link rel="stylesheet" href="http://f.example/a.css">',
    'assets/a.css': '@import url("https://fonts.example/f.css"); b{src:url(https://f.example/x.woff2)}',
    'assets/a.js': 'import("https://esm.example/m.js")',
  });
  const found = findRemoteLoads(dir);
  // 6, not 5: the CSS @import url("https://…") matches both the @import and the url( pattern.
  assert.equal(found.length, 6);
  for (const kind of ['<script', '<link', '@import', 'url(https:', 'import(']) {
    assert.ok(found.some((f) => f.includes(kind)), kind);
  }
});

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { findMockFixtures } from '../scripts/check-no-mocks.mjs';

test('flags a bundle containing a fixture marker, ignores clean files', () => {
  const dir = mkdtempSync(join(tmpdir(), 'nomocks-'));
  mkdirSync(join(dir, 'assets'));
  writeFileSync(join(dir, 'assets', 'a.js'), 'x("Weekly 1:1 — Alex")');
  writeFileSync(join(dir, 'assets', 'b.js'), 'x("hello")');
  const found = findMockFixtures(dir);
  assert.equal(found.length, 1);
  assert.ok(found[0].endsWith('Weekly 1:1 — Alex'));
});

test('second marker is caught too', () => {
  const dir = mkdtempSync(join(tmpdir(), 'nomocks-'));
  writeFileSync(join(dir, 'c.js'), 'y("Alex, Priya, Sam")');
  assert.equal(findMockFixtures(dir).length, 1);
});

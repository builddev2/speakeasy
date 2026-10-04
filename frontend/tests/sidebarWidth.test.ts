import { test } from 'node:test';
import assert from 'node:assert/strict';
import { clampSidebarWidth, parseSidebarWidth } from '../src/meetings/sidebarWidth.ts';

test('widths inside the range are kept (rounded)', () => {
  assert.equal(clampSidebarWidth(350.4, 1200), 350);
});

test('narrow drags stop at the minimum', () => {
  assert.equal(clampSidebarWidth(50, 1200), 200);
});

test('wide drags stop at the absolute maximum on a big window', () => {
  assert.equal(clampSidebarWidth(2000, 2000), 640);
});

test('wide drags leave the detail pane 360 px on a small window', () => {
  assert.equal(clampSidebarWidth(900, 800), 440);
});

test('a window too small for both keeps the sidebar minimum', () => {
  assert.equal(clampSidebarWidth(400, 400), 200);
});

test('stored widths parse; missing or corrupt ones fall back to the default', () => {
  assert.equal(parseSidebarWidth('320'), 320);
  assert.equal(parseSidebarWidth(null), null);
  assert.equal(parseSidebarWidth('abc'), null);
  assert.equal(parseSidebarWidth('-5'), null);
  assert.equal(parseSidebarWidth(''), null);
});

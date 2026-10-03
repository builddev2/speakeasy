import { test } from 'node:test';
import assert from 'node:assert/strict';
import { formatTimeRange } from '../src/meetings/timeRange.ts';

test('shared meridiem appears once', () => {
  assert.equal(formatTimeRange('9:00 AM', '9:12 AM'), '9:00–9:12 AM');
  assert.equal(formatTimeRange('4:30 PM', '5:00 PM'), '4:30–5:00 PM');
});

test('different meridiems keep both', () => {
  assert.equal(formatTimeRange('11:30 AM', '12:15 PM'), '11:30 AM–12:15 PM');
});

test('no end time or unparseable input is passed through', () => {
  assert.equal(formatTimeRange('9:00 AM', null), '9:00 AM');
  assert.equal(formatTimeRange('09:00', '10:00'), '09:00–10:00');
});

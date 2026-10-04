import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

// Notes carry no time stamps: nothing creates one and nothing is drawn beside a line.
// notesStamps.ts imports extensionless modules, so Node cannot load it headless; these
// source-level checks guard the requirement instead. Old stamps stay in the data only.
const read = (name: string) => readFileSync(new URL(`../src/meetings/${name}`, import.meta.url), 'utf8');

test('the Stamps extension draws no widgets and never stamps from a clock', () => {
  const src = read('notesStamps.ts');
  assert.doesNotMatch(src, /Decoration/);
  assert.doesNotMatch(src, /decorations/);
  assert.doesNotMatch(src, /notes-stamp/);
  assert.doesNotMatch(src, /options\.now|\bnow\b/);
  assert.doesNotMatch(src, /formatElapsed/);
  // The only stamp writes move an existing stamp between a line and its list item.
  for (const m of src.matchAll(/setNodeAttribute\([^;]*;/g)) {
    assert.match(m[0], /stamp'?, (null|first\.attrs\.stamp|item\.attrs\.stamp)\)/, m[0]);
  }
});

test('the editors take no clock and no stamp click handler', () => {
  const editor = read('NotesEditor.tsx');
  assert.doesNotMatch(editor, /\bnow\b/);
  assert.doesNotMatch(editor, /onStampClick/);
  assert.doesNotMatch(read('RecordingNotes.tsx'), /\bnow\b/);
  assert.doesNotMatch(read('MeetingDetail.tsx'), /onStampClick/);
  assert.doesNotMatch(read('NotesEditor.module.css'), /notes-stamp/);
});

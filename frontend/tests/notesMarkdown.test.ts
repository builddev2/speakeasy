import { test } from 'node:test';
import assert from 'node:assert/strict';
import { docToNotes, notesToDoc, stripStamps, type DocNode } from '../src/meetings/notesMarkdown.ts';

const SAMPLES = [
  '',
  'plain line',
  '# Title\n## Sub\nbody',
  '- one\n- two\n  - nested\n- three',
  '3. third\n4. fourth',
  '- [ ] todo\n- [x] done',
  '**bold** and *italic* and ***both***',
  '**a*b*** tail',
  'first\n\nafter blank',
  '\\- not a list\n\\# not a heading\n\\  indented',
  'stars \\* and slash \\\\',
  '- item with **bold**\n  1. inner number\n  - [ ] inner task',
  '\\*x',
  '\\* a',
  '\\\\x',
];

test('paragraphs starting with * or backslash round-trip from the doc side', () => {
  for (const text of ['*x', '* a', '\\x']) {
    const doc: DocNode = { type: 'doc', content: [{ type: 'paragraph', content: [{ type: 'text', text }] }] };
    const back = notesToDoc(docToNotes(doc));
    assert.equal(back.content![0].content![0].text, text, text);
    assert.equal(docToNotes(back).markdown, docToNotes(doc).markdown, text);
  }
});

test('canonical markdown round-trips', () => {
  for (const md of SAMPLES) {
    assert.equal(docToNotes(notesToDoc({ markdown: md, stamps: [] })).markdown, md, md);
  }
});

test('stamps stay on their lines', () => {
  const md = '# Plan\n- send deck\n  - to Alex\nwrap up';
  const stamps: [number, number][] = [[0, 3], [2, 40.5], [3, 61]];
  assert.deepEqual(docToNotes(notesToDoc({ markdown: md, stamps })), { markdown: md, stamps });
});

test('document shape', () => {
  const doc = notesToDoc({ markdown: '## H\n- [x] **done**', stamps: [[1, 9]] });
  assert.equal(doc.content![0].type, 'heading');
  assert.equal(doc.content![0].attrs!.level, 2);
  const item = doc.content![1].content![0];
  assert.equal(doc.content![1].type, 'taskList');
  assert.deepEqual([item.type, item.attrs!.checked, item.attrs!.stamp], ['taskItem', true, 9]);
  assert.deepEqual(item.content![0].content![0], { type: 'text', text: 'done', marks: [{ type: 'bold' }] });
});

test('text with markdown characters is escaped', () => {
  const doc: DocNode = { type: 'doc', content: [
    { type: 'paragraph', content: [{ type: 'text', text: '- a * b \\ c' }] },
    { type: 'paragraph', content: [{ type: 'text', text: '2. not numbered' }] },
  ] };
  const { markdown } = docToNotes(doc);
  assert.equal(markdown, '\\- a \\* b \\\\ c\n\\2. not numbered');
  assert.deepEqual(notesToDoc({ markdown, stamps: [] }).content![0].content![0].text, '- a * b \\ c');
});

test('unknown nodes and marks become plain text', () => {
  const doc: DocNode = { type: 'doc', content: [
    { type: 'blockquote', content: [{ type: 'paragraph', content: [{ type: 'text', text: 'quoted', marks: [{ type: 'link' }] }] }] },
    { type: 'paragraph', content: [{ type: 'text', text: 'x', marks: [{ type: 'strike' }] }] },
  ] };
  assert.equal(docToNotes(doc).markdown, 'quoted\nx');
});

test('stripStamps clears every stamp', () => {
  const doc = notesToDoc({ markdown: 'a\n- b', stamps: [[0, 1], [1, 2]] });
  assert.deepEqual(docToNotes(stripStamps(doc)).stamps, []);
});

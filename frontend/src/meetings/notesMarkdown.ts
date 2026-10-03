// The notepad's storage format: Markdown with one line per block, so a stamp's
// line index is simply its line number. Pure: runs under node --test.

export type Stamp = [number, number];
export interface NotesValue { markdown: string; stamps: Stamp[] }
export interface DocNode {
  type: string;
  attrs?: Record<string, unknown>;
  content?: DocNode[];
  text?: string;
  marks?: { type: string }[];
}

export const STAMPED_TYPES = ['paragraph', 'heading', 'listItem', 'taskItem'];
const LIST_TYPES = ['bulletList', 'orderedList', 'taskList'];
const MARKER = /^(\s|#{1,2} |[-*] |\d+\. |\\)/;

function escapeText(text: string): string {
  return text.replace(/[\\*]/g, (c) => `\\${c}`);
}

function inlineMarkdown(nodes: DocNode[] = []): string {
  let out = '';
  let bold = false;
  let italic = false;
  const toggle = (b: boolean, i: boolean) => {
    const db = b !== bold;
    const di = i !== italic;
    out += db && di ? '***' : db ? '**' : di ? '*' : '';
    bold = b;
    italic = i;
  };
  for (const node of nodes) {
    if (node.type !== 'text' || !node.text) continue;
    const marks = (node.marks ?? []).map((m) => m.type);
    toggle(marks.includes('bold'), marks.includes('italic'));
    out += escapeText(node.text);
  }
  toggle(false, false);
  return out;
}

function stampOf(node: DocNode): number | null {
  const s = node.attrs?.stamp;
  return typeof s === 'number' ? s : null;
}

function itemText(item: DocNode): string {
  return (item.content ?? [])
    .filter((c) => !LIST_TYPES.includes(c.type))
    .map((c) => inlineMarkdown(c.content))
    .filter((t) => t !== '')
    .join(' ');
}

export function docToNotes(doc: DocNode): NotesValue {
  const lines: string[] = [];
  const stamps: Stamp[] = [];
  const push = (line: string, node: DocNode) => {
    const s = stampOf(node);
    if (s !== null) stamps.push([lines.length, s]);
    lines.push(line);
  };
  const list = (node: DocNode, indent: string) => {
    let n = Number(node.attrs?.start ?? 1);
    for (const item of node.content ?? []) {
      const marker = node.type === 'orderedList' ? `${n++}. `
        : node.type === 'taskList' ? (item.attrs?.checked ? '- [x] ' : '- [ ] ') : '- ';
      push(indent + marker + itemText(item), item);
      for (const child of item.content ?? []) {
        if (LIST_TYPES.includes(child.type)) list(child, indent + '  ');
      }
    }
  };
  const block = (node: DocNode) => {
    if (node.type === 'heading') {
      push('#'.repeat(node.attrs?.level === 1 ? 1 : 2) + ' ' + inlineMarkdown(node.content), node);
    } else if (LIST_TYPES.includes(node.type)) {
      list(node, '');
    } else if (node.type === 'paragraph') {
      const text = inlineMarkdown(node.content);
      push(MARKER.test(text) ? `\\${text}` : text, node);
    } else {
      for (const child of node.content ?? []) block(child);   // unknown wrapper: keep its blocks
    }
  };
  for (const node of doc.content ?? []) block(node);
  if (lines.length === 1 && lines[0] === '' && stamps.length === 0) return { markdown: '', stamps };
  return { markdown: lines.join('\n'), stamps };
}

function parseInline(src: string): DocNode[] {
  const out: DocNode[] = [];
  let bold = false;
  let italic = false;
  let buf = '';
  const flush = () => {
    if (!buf) return;
    const marks = [...(bold ? [{ type: 'bold' }] : []), ...(italic ? [{ type: 'italic' }] : [])];
    out.push(marks.length ? { type: 'text', text: buf, marks } : { type: 'text', text: buf });
    buf = '';
  };
  for (let i = 0; i < src.length; i++) {
    const c = src[i];
    if (c === '\\' && i + 1 < src.length) {
      buf += src[++i];
    } else if (c === '*') {
      let n = 1;
      while (src[i + n] === '*' && n < 3) n++;
      flush();
      if (n === 3) { bold = !bold; italic = !italic; } else if (n === 2) bold = !bold; else italic = !italic;
      i += n - 1;
    } else {
      buf += c;
    }
  }
  flush();
  return out;
}

const LIST_LINE = /^( *)(?:(- \[( |x|X)\] )|([-*] )|(\d+)\. )(.*)$/;

export function notesToDoc({ markdown, stamps }: NotesValue): DocNode {
  const stampAt = new Map(stamps);
  const stamp = (i: number) => stampAt.get(i) ?? null;
  const content: DocNode[] = [];
  const stack: { level: number; type: string; node: DocNode }[] = [];
  const lines = markdown === '' ? [''] : markdown.split('\n');
  lines.forEach((line, i) => {
    const m = line.startsWith('\\') ? null : LIST_LINE.exec(line);
    if (!m) {
      stack.length = 0;
      const h = line.startsWith('\\') ? null : /^(#{1,2}) (.*)$/.exec(line);
      if (h) {
        content.push({ type: 'heading', attrs: { level: h[1].length, stamp: stamp(i) },
          content: parseInline(h[2]) });
      } else {
        const text = parseInline(line);
        content.push({ type: 'paragraph', attrs: { stamp: stamp(i) }, ...(text.length ? { content: text } : {}) });
      }
      return;
    }
    const type = m[2] ? 'taskList' : m[4] ? 'bulletList' : 'orderedList';
    let level = Math.floor(m[1].length / 2);
    while (stack.length && stack[stack.length - 1].level > level) stack.pop();
    let top = stack[stack.length - 1];
    if (top && top.level === level && top.type !== type) { stack.pop(); top = stack[stack.length - 1]; }
    const para = { type: 'paragraph', ...(parseInline(m[6]).length ? { content: parseInline(m[6]) } : {}) };
    const item: DocNode = type === 'taskList'
      ? { type: 'taskItem', attrs: { checked: m[3] !== ' ', stamp: stamp(i) }, content: [para] }
      : { type: 'listItem', attrs: { stamp: stamp(i) }, content: [para] };
    if (top && top.level === level) {
      top.node.content!.push(item);
      return;
    }
    const list: DocNode = { type, ...(type === 'orderedList' ? { attrs: { start: Number(m[5]) } } : {}), content: [item] };
    if (top) {
      const parent = top.node.content![top.node.content!.length - 1];
      parent.content!.push(list);
      level = top.level + 1;
    } else {
      content.push(list);
      level = 0;
    }
    stack.push({ level, type, node: list });
  });
  return { type: 'doc', content };
}

export function stripStamps(doc: DocNode): DocNode {
  const walk = (n: DocNode): DocNode => ({
    ...n,
    ...(n.attrs && 'stamp' in n.attrs ? { attrs: { ...n.attrs, stamp: null } } : {}),
    ...(n.content ? { content: n.content.map(walk) } : {}),
  });
  return walk(doc);
}

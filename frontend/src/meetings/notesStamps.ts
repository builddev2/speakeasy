import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
import { Mapping } from '@tiptap/pm/transform';
import type { Node as PMNode } from '@tiptap/pm/model';
import { STAMPED_TYPES } from './notesMarkdown';
import { formatElapsed } from './transcriptTurns';

interface StampOptions { now: () => number | null; onStampClick: ((seconds: number) => void) | null }
const ITEM_TYPES = ['listItem', 'taskItem'];

function isLine(node: PMNode, parent: PMNode | null): boolean {
  if (!STAMPED_TYPES.includes(node.type.name)) return false;
  return !(node.type.name === 'paragraph' && parent && ITEM_TYPES.includes(parent.type.name));
}

export const Stamps = Extension.create<StampOptions>({
  name: 'stamps',
  addOptions() {
    return { now: () => null, onStampClick: null };
  },
  addGlobalAttributes() {
    return [{ types: STAMPED_TYPES, attributes: { stamp: { default: null, keepOnSplit: false, rendered: false } } }];
  },
  addProseMirrorPlugins() {
    const options = this.options;
    return [new Plugin({
      key: new PluginKey('stamps'),
      appendTransaction(trs, oldState, state) {
        if (!trs.some((t) => t.docChanged)) return null;
        const tr = state.tr;
        // A line wrapped into a list item: the stamp sits on the inner paragraph; move it up to the item.
        state.doc.descendants((node, pos) => {
          if (!ITEM_TYPES.includes(node.type.name)) return;
          const first = node.firstChild;
          if (!first || first.type.name !== 'paragraph' || typeof first.attrs.stamp !== 'number') return;
          if (node.attrs.stamp === null) tr.setNodeAttribute(pos, 'stamp', first.attrs.stamp);
          tr.setNodeAttribute(pos + 1, 'stamp', null);
        });
        // A list item turned back into a line: the stamp goes onto the paragraph the item became.
        const mapping = new Mapping();
        trs.forEach((t) => mapping.appendMapping(t.mapping));
        oldState.doc.descendants((item, pos) => {
          if (!ITEM_TYPES.includes(item.type.name) || typeof item.attrs.stamp !== 'number') return;
          const para = item.firstChild;
          if (!para) return;
          const result = mapping.mapResult(pos + 2, 1);
          if (result.deleted || result.pos > state.doc.content.size) return;
          const $p = state.doc.resolve(result.pos);
          const target = $p.parent;
          if ($p.parentOffset !== 0 || $p.depth < 1 || !STAMPED_TYPES.includes(target.type.name)) return;
          if (!isLine(target, $p.depth > 1 ? $p.node($p.depth - 1) : null)) return;
          if (target.attrs.stamp !== null || target.textContent !== para.textContent) return;
          tr.setNodeAttribute($p.before(), 'stamp', item.attrs.stamp);
        });
        if (trs.some((t) => t.docChanged && !t.getMeta('preventUpdate'))) {
          const now = options.now();
          if (now !== null) {
            const $from = tr.doc.resolve(state.selection.from);
            for (let d = $from.depth; d > 0; d--) {
              const node = $from.node(d);
              if (!isLine(node, d > 1 ? $from.node(d - 1) : null)) continue;
              if (node.attrs.stamp === null && node.textContent.trim() !== '') {
                tr.setNodeAttribute($from.before(d), 'stamp', Math.round(now * 10) / 10);
              }
              break;
            }
          }
        }
        return tr.docChanged ? tr : null;
      },
      props: {
        decorations(state) {
          const widgets: Decoration[] = [];
          state.doc.descendants((node, pos, parent) => {
            if (!isLine(node, parent) || typeof node.attrs.stamp !== 'number') return;
            const seconds = node.attrs.stamp as number;
            widgets.push(Decoration.widget(pos + 1, () => {
              const el = document.createElement(options.onStampClick ? 'button' : 'span');
              el.className = 'notes-stamp';
              el.textContent = formatElapsed(seconds);
              el.contentEditable = 'false';
              if (options.onStampClick) {
                el.setAttribute('type', 'button');
                el.title = 'Show in transcript';
                el.addEventListener('mousedown', (e) => e.preventDefault());
                el.addEventListener('click', () => options.onStampClick?.(seconds));
              }
              return el;
            }, { side: -1, ignoreSelection: true, key: `stamp-${pos}-${seconds}` }));
          });
          return DecorationSet.create(state.doc, widgets);
        },
      },
    })];
  },
});

import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
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
      appendTransaction(trs, _old, state) {
        if (!trs.some((t) => t.docChanged && !t.getMeta('preventUpdate'))) return null;
        const now = options.now();
        if (now === null) return null;
        const $from = state.selection.$from;
        for (let d = $from.depth; d > 0; d--) {
          const node = $from.node(d);
          if (!isLine(node, d > 1 ? $from.node(d - 1) : null)) continue;
          if (node.attrs.stamp !== null || node.textContent.trim() === '') return null;
          return state.tr.setNodeAttribute($from.before(d), 'stamp', Math.round(now * 10) / 10);
        }
        return null;
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

import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Mapping } from '@tiptap/pm/transform';
import type { Node as PMNode } from '@tiptap/pm/model';
import { STAMPED_TYPES } from './notesMarkdown';

const ITEM_TYPES = ['listItem', 'taskItem'];

function isLine(node: PMNode, parent: PMNode | null): boolean {
  if (!STAMPED_TYPES.includes(node.type.name)) return false;
  return !(node.type.name === 'paragraph' && parent && ITEM_TYPES.includes(parent.type.name));
}

// Stamps are no longer shown or created, but notes saved earlier still carry them. This keeps the
// per-line `stamp` attribute (and moves it with its line) so editing never drops old data.
export const Stamps = Extension.create({
  name: 'stamps',
  addGlobalAttributes() {
    return [{ types: STAMPED_TYPES, attributes: { stamp: { default: null, keepOnSplit: false, rendered: false } } }];
  },
  addProseMirrorPlugins() {
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
        return tr.docChanged ? tr : null;
      },
    })];
  },
});

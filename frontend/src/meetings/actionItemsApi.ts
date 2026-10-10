import { bridge } from '../bridge';
import type { ActionItem, Priority } from './actionItems';
import { MOCK_TODAY } from '../mock/actionItems';

// In the browser mock there is no bridge; the items live here.
let mockItems: ActionItem[] = [];
let nextId = 1000;
const nowIso = () => new Date().toISOString();

type Update = Partial<Pick<ActionItem, 'task' | 'owner' | 'priority' | 'status' | 'notes' | 'tags'>>
  & { due?: string | null; dueReset?: boolean };
type Bulk = { status?: 'open' | 'done'; addTags?: string[]; removeTags?: string[]; delete?: boolean };

function applyUpdate(item: ActionItem, f: Update): ActionItem {
  const next: ActionItem = { ...item, updatedAt: nowIso() };
  if (f.task !== undefined) next.task = f.task;
  if (f.owner !== undefined) { next.owner = f.owner; next.mine = f.owner === 'Jason'; }
  if (f.priority !== undefined) next.priority = f.priority;
  if (f.notes !== undefined) next.notes = f.notes;
  if (f.tags !== undefined) next.tags = f.tags;
  if (f.status !== undefined && f.status !== item.status) {
    next.status = f.status;
    next.completedAt = f.status === 'done' ? nowIso() : null;
  }
  if (f.dueReset) {
    next.due = item.claudeDue;
    next.dueSource = item.claudeDue ? 'claude' : null;
  } else if (f.due !== undefined) {
    next.due = f.due;
    next.dueSource = 'user';
  }
  return next;
}

function replace(next: ActionItem): ActionItem {
  mockItems = mockItems.map((i) => (i.id === next.id ? next : i));
  return next;
}
function find(id: number): ActionItem {
  const item = mockItems.find((i) => i.id === id);
  if (!item) throw new Error(`No action item ${id}`);
  return item;
}

// Items removed in the mock, kept so restore() can bring them back.
const removed = new Map<number, ActionItem>();

export const actionItemsApi = {
  seedMock(items: ActionItem[]) { mockItems = items.map((i) => ({ ...i })); },
  list(): Promise<{ items: ActionItem[]; identitySet: boolean; today: string }> {
    if (bridge.embedded) return bridge.call('actions.list');
    return Promise.resolve({ items: mockItems, identitySet: true, today: MOCK_TODAY });
  },
  create(fields: {
    task: string; owner?: string; meetingId?: string | null; due?: string | null;
    priority?: Priority; notes?: string; tags?: string[];
  }): Promise<ActionItem> {
    if (bridge.embedded) return bridge.call('actions.create', { ...fields });
    const owner = fields.owner ?? '';
    const meeting = fields.meetingId ? mockItems.find((i) => i.meetingId === fields.meetingId) : undefined;
    const item: ActionItem = {
      id: (nextId += 1), meetingId: fields.meetingId ?? null, meetingTitle: meeting?.meetingTitle ?? null,
      meetingDate: meeting?.meetingDate ?? null, task: fields.task, owner, mine: owner === 'Jason',
      priority: fields.priority ?? 'normal', status: 'open', completedAt: null, due: fields.due ?? null,
      dueSource: fields.due ? 'user' : null, duePhrase: '', claudeDue: null, notes: fields.notes ?? '',
      tags: fields.tags ?? [], meetingTags: meeting?.meetingTags ?? [], source: 'manual',
      createdAt: nowIso(), updatedAt: nowIso(),
    };
    mockItems = [...mockItems, item];
    return Promise.resolve(item);
  },
  update(id: number, fields: Update): Promise<ActionItem> {
    if (bridge.embedded) return bridge.call('actions.update', { id, ...fields });
    try { return Promise.resolve(replace(applyUpdate(find(id), fields))); } catch (e) { return Promise.reject(e); }
  },
  remove(id: number): Promise<void> {
    if (bridge.embedded) return bridge.call('actions.delete', { id }).then(() => undefined);
    const item = mockItems.find((i) => i.id === id);
    if (item) removed.set(id, item);
    mockItems = mockItems.filter((i) => i.id !== id);
    return Promise.resolve();
  },
  restore(id: number): Promise<ActionItem> {
    if (bridge.embedded) return bridge.call('actions.restore', { id });
    const item = removed.get(id);
    if (!item) return Promise.reject(new Error(`No deleted action item ${id}`));
    removed.delete(id);
    mockItems = [...mockItems, item];
    return Promise.resolve(item);
  },
  bulk(ids: number[], change: Bulk): Promise<ActionItem[]> {
    if (bridge.embedded) return bridge.call<{ items: ActionItem[] }>('actions.bulk', { ids, ...change }).then((r) => r.items);
    const changed: ActionItem[] = [];
    for (const id of ids) {
      const item = mockItems.find((i) => i.id === id);
      if (!item) continue;
      if (change.delete) {
        removed.set(id, item);
        mockItems = mockItems.filter((i) => i.id !== id);
        changed.push(item);
        continue;
      }
      const remove = new Set((change.removeTags ?? []).map((t) => t.toLowerCase()));
      let tags = item.tags.filter((t) => !remove.has(t.toLowerCase()));
      for (const t of change.addTags ?? []) if (!tags.some((x) => x.toLowerCase() === t.toLowerCase())) tags = [...tags, t];
      changed.push(replace(applyUpdate(item, { status: change.status, tags })));
    }
    return Promise.resolve(changed);
  },
};

export const SIDEBAR_MIN = 200;
export const SIDEBAR_MAX = 640;
/** Room the detail pane keeps however wide the sidebar is dragged. */
export const DETAIL_MIN = 360;
export const SIDEBAR_KEY_STEP = 16;

/** Clamp a dragged width so neither the sidebar nor the detail pane collapses. */
export function clampSidebarWidth(width: number, containerWidth: number): number {
  const max = Math.max(SIDEBAR_MIN, Math.min(SIDEBAR_MAX, containerWidth - DETAIL_MIN));
  return Math.round(Math.min(max, Math.max(SIDEBAR_MIN, width)));
}

/** A stored width, or null (use the CSS default) when missing or corrupt. */
export function parseSidebarWidth(raw: string | null): number | null {
  if (raw === null) return null;
  const n = Number(raw);
  return Number.isFinite(n) && n > 0 ? n : null;
}

const CLOCK = /^(\d{1,2}:\d{2})\s?(AM|PM)$/;

/** "9:00–9:12 AM" when both ends share AM/PM, else both meridiems. */
export function formatTimeRange(start: string, end: string | null): string {
  if (!end) return start;
  const a = CLOCK.exec(start);
  const b = CLOCK.exec(end);
  if (a && b && a[2] === b[2]) return `${a[1]}–${b[1]} ${b[2]}`;
  return `${start}–${end}`;
}

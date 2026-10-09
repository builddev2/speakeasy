// Wait for an earlier save before sending the next value. A taken editor
// must not send queued drafts after its notes move to a saved meeting.
export function queueNotesSave<T>(
  previous: Promise<void>, value: T, closed: () => boolean,
  save: (value: T) => Promise<void>,
): Promise<void> {
  return previous.catch(() => undefined).then(() => {
    if (closed()) return;
    return save(value);
  });
}

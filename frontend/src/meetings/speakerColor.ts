export const SPEAKER_PALETTE = [
  'var(--sp-1)',
  'var(--sp-2)',
  'var(--sp-3)',
  'var(--sp-4)',
  'var(--sp-5)',
  'var(--sp-6)',
  'var(--sp-7)',
];

export function speakerColor(speakerNumber: number, colorCodeSpeakers: boolean): string {
  if (!colorCodeSpeakers) return 'var(--text-mid)';
  return SPEAKER_PALETTE[(speakerNumber - 1) % SPEAKER_PALETTE.length];
}


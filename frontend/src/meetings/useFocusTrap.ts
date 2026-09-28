import { useCallback } from 'react';
import type { KeyboardEvent, RefObject } from 'react';

function focusableElements(container: HTMLElement): HTMLElement[] {
  return Array.from(
    container.querySelectorAll<HTMLElement>('input, button, [tabindex]:not([tabindex="-1"])'),
  ).filter((el) => !el.hasAttribute('disabled'));
}

/**
 * Shared Tab-trap + Esc-to-close keydown handler for an overlay (sheet or
 * popover). Escape stops propagation so it only ever closes the overlay it
 * was pressed in, never something behind it. Spread the returned handler
 * onto the overlay's outermost element.
 */
export function useFocusTrap(containerRef: RefObject<HTMLElement | null>, onEscape: () => void) {
  return useCallback(
    (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        onEscape();
        return;
      }
      if (e.key === 'Tab' && containerRef.current) {
        const focusable = focusableElements(containerRef.current);
        if (focusable.length === 0) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    },
    [containerRef, onEscape],
  );
}

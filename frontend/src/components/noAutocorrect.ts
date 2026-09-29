import type { InputHTMLAttributes } from 'react';

/**
 * Spread onto every free-text <input>/<textarea>. WKWebView inherits macOS
 * system text replacement, so without this typing "cognos" is rewritten to
 * "Cognoscenti" when focus leaves the field. autoCorrect/autoCapitalize are
 * non-standard, hence the typed const rather than inline props.
 */
export const NO_AUTOCORRECT: InputHTMLAttributes<HTMLInputElement> = {
  spellCheck: false,
  autoCorrect: 'off',
  autoCapitalize: 'off',
  autoComplete: 'off',
};

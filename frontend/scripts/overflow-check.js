// Lists elements whose content is cut off: horizontal page scroll, text or
// buttons wider than their box without an ellipsis, and boxes poking out of
// the window. Paste into the page console or run via the browser tool.
(() => {
  const out = [];
  const W = document.documentElement.clientWidth;
  if (document.documentElement.scrollWidth > W + 1) out.push(`page scrolls sideways: ${document.documentElement.scrollWidth} > ${W}`);
  for (const el of document.querySelectorAll('body *')) {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    const clipsX = el.scrollWidth > el.clientWidth + 1 && cs.overflowX !== 'visible'
      && cs.overflowX !== 'auto' && cs.overflowX !== 'scroll' && cs.textOverflow !== 'ellipsis';
    const isControl = el.matches('button, [role="button"], [role="tab"], [role="radio"], input, label');
    if (clipsX && (isControl || el.children.length === 0)) out.push(`clipped: ${el.tagName.toLowerCase()}.${el.getAttribute('class')} "${(el.textContent || '').trim().slice(0, 40)}"`);
    if (isControl && el.scrollWidth > Math.ceil(r.width) + 1) out.push(`control narrower than its text: "${(el.textContent || '').trim().slice(0, 40)}"`);
    if (r.right > W + 1 && cs.position !== 'fixed') out.push(`outside the window: ${el.tagName.toLowerCase()}.${el.getAttribute('class')}`);
  }
  return [...new Set(out)];
})();

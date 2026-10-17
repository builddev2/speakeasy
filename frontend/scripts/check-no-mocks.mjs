// Fails the build when mock fixtures (dev-only sample meetings) leak into the
// production bundle. They are gated behind import.meta.env.DEV so Rollup drops them.
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const MARKERS = ['Weekly 1:1 — Alex', 'Alex, Priya, Sam'];

function files(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? files(path) : [path];
  });
}

export function findMockFixtures(dir) {
  const out = [];
  for (const path of files(dir)) {
    if (!path.endsWith('.js')) continue;
    const text = readFileSync(path, 'utf8');
    for (const marker of MARKERS) if (text.includes(marker)) out.push(`${path}: ${marker}`);
  }
  return out;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const found = findMockFixtures(fileURLToPath(new URL('../dist', import.meta.url)));
  if (found.length) {
    console.error('Mock fixtures in the production bundle:\n' + found.join('\n'));
    process.exit(1);
  }
}

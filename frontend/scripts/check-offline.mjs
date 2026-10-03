// Fails the build when the bundle would load anything from the network
// (Speakeasy must run fully offline). URLs inside ordinary strings, such as
// React's error-docs links, are not loads and are allowed.
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const PATTERNS = {
  '.html': [/<script[^>]+src=["']?https?:/gi, /<link[^>]+href=["']?https?:/gi],
  '.css': [/@import\s+(?:url\()?\s*["']?https?:/gi, /url\(\s*["']?https?:/gi],
  '.js': [/import\(\s*["']https?:/gi, /importScripts\(\s*["']https?:/gi],
};

function files(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? files(path) : [path];
  });
}

export function findRemoteLoads(dir) {
  const out = [];
  for (const path of files(dir)) {
    const ext = Object.keys(PATTERNS).find((e) => path.endsWith(e));
    if (!ext) continue;
    const text = readFileSync(path, 'utf8');
    for (const re of PATTERNS[ext]) for (const m of text.matchAll(re)) out.push(`${path}: ${m[0]}`);
  }
  return out;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const found = findRemoteLoads(fileURLToPath(new URL('../dist', import.meta.url)));
  if (found.length) {
    console.error('Remote loads in the bundle (must be offline):\n' + found.join('\n'));
    process.exit(1);
  }
}

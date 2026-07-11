import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';

export default defineConfig({
  base: './',
  plugins: [react()],
  build: {
    rollupOptions: {
      input: {
        index: fileURLToPath(new URL('./index.html', import.meta.url)),
        dock: fileURLToPath(new URL('./dock.html', import.meta.url)),
        meetings: fileURLToPath(new URL('./meetings.html', import.meta.url)),
        training: fileURLToPath(new URL('./training.html', import.meta.url)),
      },
    },
  },
});

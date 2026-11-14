import { resolve } from 'node:path';

import react from '@vitejs/plugin-react';
import { defineConfig } from 'electron-vite';
import type { Plugin } from 'vite';

/** Dev server needs inline styles (HMR style tags), the React refresh preamble and a websocket. Never applied to the prod build. */
function devCsp(): Plugin {
  return {
    name: 'asclepius-dev-csp',
    apply: 'serve',
    transformIndexHtml: {
      order: 'pre',
      handler(html) {
        return html
          .replace("script-src 'self'", "script-src 'self' 'unsafe-inline'")
          .replace("style-src 'self'", "style-src 'self' 'unsafe-inline'")
          .replace("connect-src 'self'", "connect-src 'self' ws://localhost:* http://localhost:*");
      },
    },
  };
}

export default defineConfig({
  main: {
    build: {
      rolldownOptions: { input: resolve(import.meta.dirname, 'src/main/index.ts') },
    },
  },
  preload: {
    build: {
      rolldownOptions: {
        input: resolve(import.meta.dirname, 'src/preload/index.ts'),
        // Sandboxed preload scripts must be CommonJS.
        output: { format: 'cjs', entryFileNames: '[name].cjs' },
      },
    },
  },
  renderer: {
    root: resolve(import.meta.dirname, 'src/renderer'),
    plugins: [react(), devCsp()],
    css: { modules: { localsConvention: 'camelCaseOnly' } },
    build: {
      assetsInlineLimit: 0,
      rolldownOptions: { input: resolve(import.meta.dirname, 'src/renderer/index.html') },
    },
  },
});

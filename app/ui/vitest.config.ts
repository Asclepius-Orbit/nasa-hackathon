import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    coverage: {
      provider: 'v8',
      include: ['src/**/*.{ts,tsx}'],
      exclude: [
        'src/contracts/**',
        '**/*.d.ts',
        '**/*.test.{ts,tsx}',
        'src/renderer/test/**',
        // Entry stubs: bootstrap only, covered by e2e.
        'src/renderer/main.tsx',
        'src/main/index.ts',
        'src/preload/index.ts',
        'e2e/**',
      ],
      thresholds: { lines: 90, branches: 90, functions: 90, statements: 90 },
    },
    projects: [
      {
        test: {
          name: 'main',
          environment: 'node',
          include: ['src/main/**/*.test.ts', 'src/shared/**/*.test.ts'],
        },
      },
      {
        plugins: [react()],
        test: {
          name: 'renderer',
          environment: 'jsdom',
          include: ['src/renderer/**/*.test.{ts,tsx}'],
          setupFiles: ['src/renderer/test/setup.ts'],
        },
      },
    ],
  },
});

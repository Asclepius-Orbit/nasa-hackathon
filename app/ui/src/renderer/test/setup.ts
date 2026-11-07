import '@testing-library/jest-dom/vitest';

import { cleanup, configure } from '@testing-library/react';
import { afterEach } from 'vitest';

// Vitest globals are off, so Testing Library cannot register its own cleanup.
afterEach(cleanup);

// findBy*/waitFor default to 1 s, which is too tight on a loaded CI/Docker CPU.
configure({ asyncUtilTimeout: 5_000 });

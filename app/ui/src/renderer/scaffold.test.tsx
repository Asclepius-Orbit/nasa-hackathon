// Scaffold sanity test: proves the renderer (jsdom) test project runs. Replace with real tests.
import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';

import { App } from './App';

it('scaffold sanity: renders the app heading', () => {
  render(<App />);
  expect(screen.getByRole('heading', { name: 'Asclepius Orbit' })).toBeInTheDocument();
});

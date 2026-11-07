import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';

import { NoData, Panel } from './Panel';

it('renders its children in a div by default', () => {
  render(<Panel>inside</Panel>);
  expect(screen.getByText('inside').tagName).toBe('DIV');
});

it('can render as another landmark-friendly element', () => {
  render(
    <Panel as="section" aria-label="Chart">
      inside
    </Panel>,
  );
  expect(screen.getByRole('region', { name: 'Chart' })).toBeInTheDocument();
});

it('NoData says so explicitly, never blank', () => {
  render(<NoData />);
  expect(screen.getByText('No data')).toBeInTheDocument();
  render(<NoData>Not assessed yet</NoData>);
  expect(screen.getByText('Not assessed yet')).toBeInTheDocument();
});

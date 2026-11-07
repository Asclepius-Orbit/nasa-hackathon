import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { FakeAsclepiusApi } from '../test/fake-api';
import { ApiProvider, useApi } from './ApiContext';

function CrewCount(): React.JSX.Element {
  const api = useApi();
  void api.crew.list();
  return <p>{api === window.asclepius ? 'window api' : 'injected api'}</p>;
}

afterEach(() => {
  // @ts-expect-error cleaning the test double off the global
  delete window.asclepius;
});

describe('ApiProvider', () => {
  it('gives components the injected api', () => {
    const fake = new FakeAsclepiusApi();
    render(
      <ApiProvider api={fake}>
        <CrewCount />
      </ApiProvider>,
    );
    expect(screen.getByText('injected api')).toBeInTheDocument();
    expect(fake.calls).toContain('crew.list');
  });

  it('falls back to window.asclepius from the preload bridge', () => {
    window.asclepius = new FakeAsclepiusApi();
    render(
      <ApiProvider>
        <CrewCount />
      </ApiProvider>,
    );
    expect(screen.getByText('window api')).toBeInTheDocument();
  });

  it('useApi works without a provider, using the bridge', () => {
    window.asclepius = new FakeAsclepiusApi();
    render(<CrewCount />);
    expect(screen.getByText('window api')).toBeInTheDocument();
  });
});

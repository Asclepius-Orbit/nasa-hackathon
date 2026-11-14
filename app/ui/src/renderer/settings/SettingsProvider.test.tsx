import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it } from 'vitest';

import { ApiProvider } from '../api/ApiContext';
import { FakeAsclepiusApi } from '../test/fake-api';
import { SettingsProvider, useSettings } from './SettingsProvider';

function Probe(): React.JSX.Element {
  const { settings, loaded, update, formatLength, formatTime } = useSettings();
  return (
    <div>
      <p>{loaded ? 'loaded' : 'loading'}</p>
      <p data-testid="size">{settings.textSize}</p>
      <p data-testid="len">{formatLength(2.1)}</p>
      <p data-testid="time">{formatTime(new Date('2026-11-05T13:40:00Z'), new Date('2026-08-11T00:00:00Z'))}</p>
      <button
        type="button"
        onClick={() => {
          void update({ textSize: 'standard', lengthUnit: 'in', timeFormat: 'gmt_met' });
        }}
      >
        change
      </button>
    </div>
  );
}

const setup = (api = new FakeAsclepiusApi()): FakeAsclepiusApi => {
  render(
    <ApiProvider api={api}>
      <SettingsProvider>
        <Probe />
      </SettingsProvider>
    </ApiProvider>,
  );
  return api;
};

afterEach(() => {
  document.documentElement.removeAttribute('data-text-size');
});

it('shows defaults until the stored settings arrive', async () => {
  setup();
  expect(screen.getByText('loading')).toBeInTheDocument();
  expect(screen.getByTestId('size')).toHaveTextContent('large');
  await screen.findByText('loaded');
});

it('applies the stored text size to the document root', async () => {
  const api = new FakeAsclepiusApi();
  api.settingsValue = { ...api.settingsValue, textSize: 'standard' };
  setup(api);
  await waitFor(() => {
    expect(document.documentElement.dataset.textSize).toBe('standard');
  });
});

it('saves a change through the api and re-applies it', async () => {
  const api = setup();
  await screen.findByText('loaded');
  expect(screen.getByTestId('len')).toHaveTextContent('+2.1 mm');
  await userEvent.click(screen.getByRole('button', { name: 'change' }));
  await waitFor(() => {
    expect(screen.getByTestId('len')).toHaveTextContent('+0.08 in');
  });
  expect(api.settingsValue.textSize).toBe('standard');
  expect(document.documentElement.dataset.textSize).toBe('standard');
  expect(screen.getByTestId('time')).toHaveTextContent('13:40, MET 86 d 13:40');
});

it('keeps the defaults when the settings cannot be read', async () => {
  const api = new FakeAsclepiusApi();
  api.settings.get = () => Promise.reject(new Error('main unavailable'));
  setup(api);
  await screen.findByText('loaded');
  expect(screen.getByTestId('size')).toHaveTextContent('large');
});

it('useSettings outside a provider fails loudly', () => {
  expect(() => render(<Probe />)).toThrow(/SettingsProvider/);
});

import { act, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it } from 'vitest';

import { MockDataSource } from '../../data/mock/MockDataSource';
import type { StripData } from '../../data/types';
import { FakeAsclepiusApi } from '../../test/fake-api';
import { renderInWorld } from '../../test/providers';
import { Strip } from './Strip';

class StripVariant extends MockDataSource {
  constructor(private readonly patch: Partial<StripData>) {
    super();
  }
  override getStrip(): Promise<StripData> {
    return super.getStrip().then((s) => ({ ...s, ...this.patch }));
  }
}

it('shows who is signed in and the mission facts from the data source', async () => {
  renderInWorld(<Strip />);
  expect(await screen.findByText('Elena Ruiz')).toBeInTheDocument();
  expect(await screen.findByText('87')).toBeInTheDocument();
  expect(screen.getByText('Mission day')).toBeInTheDocument();
  expect(screen.getByText('In flight, microgravity')).toBeInTheDocument();
  expect(screen.getByText('Harness wb-01')).toBeInTheDocument();
  expect(screen.getByText('64 %, EMG right weak')).toBeInTheDocument();
  expect(screen.getByText('Ground link')).toBeInTheDocument();
  expect(screen.getByText('Down, back in 12 min')).toBeInTheDocument();
});

it('ticks the GMT clock with the injected clock', async () => {
  const { clock } = renderInWorld(<Strip />);
  expect(await screen.findByText('13:40')).toBeInTheDocument();
  act(() => {
    clock.advance(60_000);
  });
  expect(screen.getByText('13:41')).toBeInTheDocument();
});

it('adds mission elapsed time when the setting asks for it', async () => {
  const api = new FakeAsclepiusApi();
  api.settingsValue = { ...api.settingsValue, timeFormat: 'gmt_met' };
  renderInWorld(<Strip />, { api });
  expect(await screen.findByText('13:40, MET 86 d 13:40')).toBeInTheDocument();
});

it('says "Not paired yet" when there is no harness, never a battery figure', async () => {
  renderInWorld(<Strip />, { data: new StripVariant({ harness: null }) });
  expect(await screen.findByText('Not paired yet')).toBeInTheDocument();
  expect(screen.queryByText(/%/)).not.toBeInTheDocument();
});

it('shows the ground link as up when it is', async () => {
  renderInWorld(<Strip />, { data: new StripVariant({ groundLink: { state: 'up' } }) });
  expect(await screen.findByText('Up')).toBeInTheDocument();
});

it('falls back to the crew code when the name is unknown', async () => {
  const api = new FakeAsclepiusApi();
  api.session = { crewId: 'AST-09', locked: false };
  renderInWorld(<Strip />, { api });
  expect(await screen.findByText('AST-09')).toBeInTheDocument();
});

it('always offers the red Urgent symptom button, which opens the dialog', async () => {
  renderInWorld(<Strip />);
  await userEvent.click(screen.getByRole('button', { name: 'Urgent symptom' }));
  expect(screen.getByRole('dialog', { name: 'What do you have now?' })).toBeInTheDocument();
});

it('keeps the Urgent button even if the crew list cannot be read', async () => {
  const api = new FakeAsclepiusApi();
  api.crew.list = () => Promise.reject(new Error('unavailable'));
  renderInWorld(<Strip />, { api });
  expect(screen.getByRole('button', { name: 'Urgent symptom' })).toBeInTheDocument();
  expect(await screen.findByText('AST-01')).toBeInTheDocument();
});

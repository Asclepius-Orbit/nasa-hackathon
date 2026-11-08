import { act, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it } from 'vitest';

import { RealDataSource } from '../../data/real/RealDataSource';
import { MockDataSource } from '../../data/mock/MockDataSource';
import type { StripData } from '../../data/types';
import type { StripView } from '../../../shared/api';
import { example } from '../../test/contract-examples';
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
  expect(screen.getByText('64 %, EMG right latissimus stalled')).toBeInTheDocument();
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

it('locks the tablet from the strip, hiding health data behind the PIN', async () => {
  const api = new FakeAsclepiusApi();
  renderInWorld(<Strip />, { api });
  await userEvent.click(await screen.findByRole('button', { name: 'Lock' }));
  expect(await api.auth.current()).toMatchObject({ locked: true });
});

it('shows an unknown ground link as Not known, never as up', async () => {
  renderInWorld(<Strip />, { data: new StripVariant({ groundLink: null }) });
  expect(await screen.findByText('Not known')).toBeInTheDocument();
  expect(screen.queryByText('Up')).not.toBeInTheDocument();
});

it('shows a weak battery reading, degraded status and weak streams as data from the backend', async () => {
  const api = new FakeAsclepiusApi();
  api.viewData.strip = example<StripView>('ui-strip');
  renderInWorld(<Strip />, { api, data: new RealDataSource(api.views) });
  expect(await screen.findByText('18 %, degraded, Stretch cord L1 to L5 stalled')).toBeInTheDocument();
  expect(screen.getByText('Not known')).toBeInTheDocument();
  expect(screen.getByText('87')).toBeInTheDocument();
  expect(screen.getByText('In flight, microgravity')).toBeInTheDocument();
});

it('shows no data for what the backend does not know: no launch date, no phase, no battery', async () => {
  const api = new FakeAsclepiusApi();
  api.viewData.strip = example<StripView>('ui-strip.unpaired');
  api.settingsValue = { ...api.settingsValue, timeFormat: 'gmt_met' };
  renderInWorld(<Strip />, { api, data: new RealDataSource(api.views) });
  expect(await screen.findByText('Not paired yet')).toBeInTheDocument();
  expect(screen.getAllByText('No data')).toHaveLength(2);
  expect(screen.getByText('13:40, MET not known')).toBeInTheDocument();
});

it('says there is no connection to the monitor when the backend is down, and still offers Urgent', async () => {
  const api = new FakeAsclepiusApi();
  renderInWorld(<Strip />, { api, data: new RealDataSource(api.views) });
  expect(await screen.findByText('No connection to the monitor')).toBeInTheDocument();
  expect(screen.queryByText('Mission day')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Urgent symptom' })).toBeInTheDocument();
});

it('on a locked bridge the strip does not claim the monitor is down', async () => {
  const api = new FakeAsclepiusApi();
  api.viewErrors = { strip: 'locked' };
  renderInWorld(<Strip />, { api, data: new RealDataSource(api.views) });
  await screen.findByRole('button', { name: 'Urgent symptom' });
  await waitFor(() => {
    expect(api.calls.filter((c) => c === 'auth.current').length).toBeGreaterThan(1);
  });
  expect(screen.queryByText('No connection to the monitor')).not.toBeInTheDocument();
});

it('shows a harness that has gone quiet as last seen, and does not present its battery as current', async () => {
  const api = new FakeAsclepiusApi();
  const strip = example<StripView>('ui-strip');
  api.viewData.strip = { ...strip, harness: strip.harness && { ...strip.harness, stale: true, last_seen_at: '2026-11-03T07:15:00Z' } };
  renderInWorld(<Strip />, { api, data: new RealDataSource(api.views) });
  expect(await screen.findByText('Last seen 3 Nov 07:15 GMT')).toBeInTheDocument();
  expect(screen.queryByText(/18 %/)).not.toBeInTheDocument();
  expect(screen.queryByText(/degraded/)).not.toBeInTheDocument();
});

it('writes only the time for a harness last seen today', async () => {
  const api = new FakeAsclepiusApi();
  const strip = example<StripView>('ui-strip');
  api.viewData.strip = { ...strip, harness: strip.harness && { ...strip.harness, stale: true, last_seen_at: '2026-11-05T07:15:00Z' } };
  renderInWorld(<Strip />, { api, data: new RealDataSource(api.views) });
  expect(await screen.findByText('Last seen 07:15 GMT')).toBeInTheDocument();
});

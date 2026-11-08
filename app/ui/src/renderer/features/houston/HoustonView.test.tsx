import { screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { MockDataSource } from '../../data/mock/MockDataSource';
import { RealDataSource } from '../../data/real/RealDataSource';
import { FakeAsclepiusApi } from '../../test/fake-api';
import { FakeClock } from '../../test/fake-clock';
import { FakeScheduler } from '../../test/fake-scheduler';
import { renderInWorld } from '../../test/providers';
import { HoustonView } from './HoustonView';

describe('HoustonView', () => {
  it('shows the day summary under the crew code, never a name', async () => {
    renderInWorld(<HoustonView />);
    expect(await screen.findByRole('heading', { name: 'Day 87 summary' })).toBeInTheDocument();
    expect(screen.getByText(/It uses your crew code, AST-01, never your name/)).toBeInTheDocument();
    expect(screen.getByText('Amber')).toBeInTheDocument();
    expect(screen.getByText(/confidence 70 %, data 8 of 10/)).toBeInTheDocument();
    expect(screen.getByText('Lumbar length +2.1 mm, curve flatter, pain 7-day mean 4')).toBeInTheDocument();
    expect(screen.getByText('None reported')).toBeInTheDocument();
    expect(screen.getByText('1 in progress')).toBeInTheDocument();
    expect(screen.getByText('None today')).toBeInTheDocument();
    expect(screen.getByText('REC_LUMBAR_STAB_01, REC_SESSION_REPEAT_01')).toBeInTheDocument();
    expect(screen.queryByText(/Elena/)).toBeNull();
  });

  it('uses the crew code of the session, not a fixed one', async () => {
    const api = new FakeAsclepiusApi();
    api.session = { crewId: 'AST-02', locked: false };
    renderInWorld(<HoustonView />, { api });
    expect(await screen.findByText(/It uses your crew code, AST-02, never your name/)).toBeInTheDocument();
  });

  it('lists the outbox as Queued or Sent with the received time', async () => {
    renderInWorld(<HoustonView />);
    const outbox = await screen.findByRole('list', { name: 'Outbox' });
    const rows = within(outbox).getAllByRole('listitem');
    expect(rows).toHaveLength(3);
    expect(within(rows[0] as HTMLElement).getByText('Queued')).toBeInTheDocument();
    expect(within(rows[1] as HTMLElement).getByText('Day 86 summary')).toBeInTheDocument();
    expect(within(rows[1] as HTMLElement).getByText('Sent, received 21:04')).toBeInTheDocument();
    expect(screen.getByText('Ground link down, back in 12 min. Queued items go out in order.')).toBeInTheDocument();
  });

  it('shows a call to the crew medical officer in Contacts', async () => {
    const clock = new FakeClock('2026-11-05T13:40:00Z');
    const data = new MockDataSource({ now: () => clock.now(), scheduler: new FakeScheduler() });
    await data.logCrewMedicalOfficerCall();
    renderInWorld(<HoustonView />, { data, clock });
    expect(await screen.findByText(/Crew medical officer called at/)).toBeInTheDocument();
  });

  it('says so when the summary cannot be loaded', async () => {
    const data = new MockDataSource({ now: () => new Date(), scheduler: new FakeScheduler() });
    data.getHouston = () => Promise.reject(new Error('x'));
    renderInWorld(<HoustonView />, { data });
    expect(await screen.findByRole('alert')).toHaveTextContent('The Houston summary could not be loaded.');
  });

  it('handles an unknown return time and an up link', async () => {
    const data = new MockDataSource({ now: () => new Date(), scheduler: new FakeScheduler() });
    const real = await data.getHouston();
    data.getHouston = () =>
      Promise.resolve({ ...real, groundLink: { state: 'up' }, outbox: [{ label: 'Day 86 summary', state: 'sent', receivedGmt: null }] });
    renderInWorld(<HoustonView />, { data });
    expect(await screen.findByText('Sent')).toBeInTheDocument();
    expect(screen.getByText('Ground link up. Queued items go out in order.')).toBeInTheDocument();
  });
});

describe('HoustonView with the backend (real mode)', () => {
  it('says it is not available: no summary is invented', async () => {
    const api = new FakeAsclepiusApi();
    renderInWorld(<HoustonView />, { api, data: new RealDataSource(api.views) });
    expect(await screen.findByTestId('not-built')).toBeInTheDocument();
  expect(screen.getByText('Not available yet.')).toBeInTheDocument();
  expect(screen.queryByText(/did not answer/)).not.toBeInTheDocument();
    expect(screen.queryByText(/summary/i)).not.toBeInTheDocument();
    expect(screen.queryByText('None reported')).not.toBeInTheDocument();
  });
});

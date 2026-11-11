import { act, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { HoustonView as HoustonContract, OutboxView } from '../../../shared/api';
import { MockDataSource } from '../../data/mock/MockDataSource';
import { RealDataSource } from '../../data/real/RealDataSource';
import type { HoustonData } from '../../data/types';
import { example } from '../../test/contract-examples';
import { FakeAsclepiusApi } from '../../test/fake-api';
import { FakeScheduler } from '../../test/fake-scheduler';
import { renderInWorld } from '../../test/providers';
import { HoustonView } from './HoustonView';

const PENDING = 'Pending validation by Aguado';
const SIMULATED = 'Simulated ground link: nothing leaves this computer';

/** The backend, answering with the contract examples (or the given variants). */
function backend(houston = 'ui-houston', outbox = 'ui-outbox'): { api: FakeAsclepiusApi; data: RealDataSource } {
  const api = new FakeAsclepiusApi();
  api.viewData.houston = example<HoustonContract>(houston);
  api.viewData.outbox = example<OutboxView>(outbox);
  return { api, data: new RealDataSource(api.views, api.reps, api) };
}

/** A mock whose Houston data is changed by `patch`. */
function mockWith(patch: (real: HoustonData) => HoustonData): MockDataSource {
  const data = new MockDataSource({ now: () => new Date('2026-11-05T13:40:00Z'), scheduler: new FakeScheduler() });
  const real = data.getHouston.bind(data);
  data.getHouston = async () => patch(await real());
  return data;
}

describe('HoustonView with the contract examples', () => {
  it('writes the summary from codes under the crew code, never a name', async () => {
    const { api, data } = backend();
    renderInWorld(<HoustonView />, { api, data });
    expect(await screen.findByRole('heading', { name: 'Day 87 summary' })).toBeInTheDocument();
    expect(screen.getByText(/It uses your crew code, AST-01, never your name/)).toBeInTheDocument();
    expect(screen.getByText('Amber')).toBeInTheDocument();
    expect(screen.getByText(/confidence 74 %, data 5 of 6/)).toBeInTheDocument();
    expect(screen.getByText(/Lumbar length/)).toBeInTheDocument();
    expect(screen.getByText(/Lumbar stabilisation exercises/)).toBeInTheDocument();
    expect(screen.getByText('Not available yet')).toBeInTheDocument();
  });

  it('says in words, not only colour, that the content is unvalidated and the link simulated', async () => {
    const { api, data } = backend();
    renderInWorld(<HoustonView />, { api, data });
    expect(await screen.findByText(PENDING)).toBeInTheDocument();
    expect(screen.getByText(SIMULATED)).toBeInTheDocument();
  });

  it('lists the outbox by day and revision with Queued / Sent and the received time in GMT', async () => {
    const { api, data } = backend();
    renderInWorld(<HoustonView />, { api, data });
    const rows = within(await screen.findByRole('list', { name: 'Outbox' })).getAllByRole('listitem');
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent('Day 87 summary, revision 2');
    expect(within(rows[0] as HTMLElement).getByText('Queued')).toBeInTheDocument();
    expect(rows[1]).toHaveTextContent(/^Day 87 summary(?!, revision)/);
    expect(rows[1]).toHaveTextContent('Sent');
    expect(rows[1]).toHaveTextContent(/received 9 Feb 06:00 GMT/);
    expect(rows[2]).toHaveTextContent(/^Day 86 summary(?!, revision)/);
  });

  it('says the ground link is down with the minutes back, and the last contact', async () => {
    const { api, data } = backend();
    renderInWorld(<HoustonView />, { api, data });
    expect(await screen.findByText(/Ground link down, back in 12 min\./)).toBeInTheDocument();
    expect(screen.getByText(/Last contact/)).toBeInTheDocument();
  });

  it('shows an explicit empty state: no summary, unknown link, empty outbox, nothing green', async () => {
    const { api, data } = backend('ui-houston.empty', 'ui-outbox.empty');
    renderInWorld(<HoustonView />, { api, data });
    expect(await screen.findByText('No summary yet')).toBeInTheDocument();
    expect(screen.getByText('Ground link: not known. Nothing is sent.')).toBeInTheDocument();
    expect(screen.getByText('Nothing in the outbox yet.')).toBeInTheDocument();
    expect(screen.queryByText('Green')).not.toBeInTheDocument();
    expect(screen.queryByText(/Last contact/)).not.toBeInTheDocument();
    expect(screen.getByText(PENDING)).toBeInTheDocument();
    expect(screen.getByText(SIMULATED)).toBeInTheDocument();
  });

  it('titles a summary without a mission day by its date', async () => {
    const { api, data } = backend();
    const base = example<HoustonContract>('ui-houston');
    api.viewData.houston = { ...base, summary: base.summary && { ...base.summary, mission_day: null } };
    renderInWorld(<HoustonView />, { api, data });
    expect(await screen.findByRole('heading', { name: '2027-02-09' })).toBeInTheDocument();
  });

  it('shows No data for a nodata day, with no confidence and no green', async () => {
    const { api, data } = backend();
    const base = example<HoustonContract>('ui-houston');
    api.viewData.houston = {
      ...base,
      summary: base.summary && {
        ...base.summary,
        level: 'nodata',
        confidence: null,
        data_available: null,
        data_total: null,
        drivers: [],
        recommendations: [],
        escalation: null,
      },
    };
    renderInWorld(<HoustonView />, { api, data });
    expect(await screen.findByText('No data')).toBeInTheDocument();
    expect(screen.queryByText(/confidence 0/)).not.toBeInTheDocument();
    expect(screen.queryByText('Green')).not.toBeInTheDocument();
  });

  it('writes the red flags with the shared labels', async () => {
    const { api, data } = backend();
    const base = example<HoustonContract>('ui-houston');
    api.viewData.houston = { ...base, summary: base.summary && { ...base.summary, red_flags: ['leg_weakness'] } };
    renderInWorld(<HoustonView />, { api, data });
    expect(await screen.findByText('Leg weakness')).toBeInTheDocument();
  });

  it('shows an up link as up', async () => {
    const { api, data } = backend();
    const base = example<HoustonContract>('ui-houston');
    api.viewData.houston = { ...base, ground_link: { state: 'up' } };
    renderInWorld(<HoustonView />, { api, data });
    expect(await screen.findByText('Ground link up.')).toBeInTheDocument();
  });

  it('says a down link with no return time plainly', async () => {
    const { api, data } = backend();
    const base = example<HoustonContract>('ui-houston');
    api.viewData.houston = { ...base, ground_link: { state: 'down', back_in_min: null } };
    renderInWorld(<HoustonView />, { api, data });
    expect(await screen.findByText('Ground link down.')).toBeInTheDocument();
  });

  it('reloads on its own: the queue changes to Sent without leaving the screen', async () => {
    const { api, data } = backend();
    renderInWorld(<HoustonView />, { api, data });
    await screen.findByRole('list', { name: 'Outbox' });
    const outbox = example<OutboxView>('ui-outbox');
    api.viewData.outbox = { ...outbox, items: outbox.items.map((i) => ({ ...i, state: 'sent', received_at: '2027-02-09T09:10:00Z' })) };
    await act(async () => {
      window.dispatchEvent(new Event('focus'));
      await Promise.resolve();
    });
    expect(await screen.findAllByText(/received 9 Feb 09:10 GMT/)).not.toHaveLength(0);
    expect(screen.queryByText('Queued')).not.toBeInTheDocument();
  });
});

describe('HoustonView with the mock', () => {
  it('uses the crew code of the session, not a fixed one', async () => {
    const api = new FakeAsclepiusApi();
    api.session = { crewId: 'AST-02', locked: false };
    renderInWorld(<HoustonView />, { api });
    expect(await screen.findByText(/It uses your crew code, AST-02, never your name/)).toBeInTheDocument();
  });

  it('shows the mock day 87 with the same notices', async () => {
    renderInWorld(<HoustonView />);
    expect(await screen.findByRole('heading', { name: 'Day 87 summary' })).toBeInTheDocument();
    expect(screen.getByText(PENDING)).toBeInTheDocument();
    expect(screen.getByText(SIMULATED)).toBeInTheDocument();
    expect(screen.queryByText(/Elena/)).toBeNull();
  });

  it('drops the pending notice only when the content is validated', async () => {
    renderInWorld(<HoustonView />, { data: mockWith((real) => ({ ...real, contentValidated: true })) });
    await screen.findByText(SIMULATED);
    expect(screen.queryByText(PENDING)).not.toBeInTheDocument();
  });

  it('says so when the summary cannot be loaded', async () => {
    const data = mockWith((real) => real);
    data.getHouston = () => Promise.reject(new Error('x'));
    renderInWorld(<HoustonView />, { data });
    expect(await screen.findByRole('alert')).toHaveTextContent('The Houston summary could not be loaded.');
  });

  it('shows the no-summary state when there is none', async () => {
    renderInWorld(<HoustonView />, { data: mockWith((real) => ({ ...real, summary: null })) });
    expect(await screen.findByText('No summary yet')).toBeInTheDocument();
  });
});

describe('HoustonView honesty and readability', () => {
  it.each(['', 'radio-v2'])('shows the simulated-link notice unless the transport is a known real one (transport %j)', async (transport) => {
    renderInWorld(<HoustonView />, { data: mockWith((real) => ({ ...real, transport })) });
    expect(await screen.findByText(SIMULATED)).toBeInTheDocument();
  });

  it('keeps list keys unique when two items read the same', async () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const data = mockWith((real) => ({
      ...real,
      summary: real.summary && { ...real.summary, recommendations: ['REC_LUMBAR_STAB_01', 'REC_LUMBAR_STAB_01'] },
    }));
    renderInWorld(<HoustonView />, { data });
    await screen.findByText(SIMULATED);
    expect(spy.mock.calls.flat().join(' ')).not.toMatch(/same key/);
    spy.mockRestore();
  });

  it('writes the receipt of another day with its GMT date, and today with the time only', async () => {
    const data = mockWith((real) => ({
      ...real,
      outbox: real.outbox.slice(0, 2).map((item, i) => ({
        ...item,
        state: 'sent' as const,
        receivedAt: i === 0 ? '2026-11-03T09:58:00Z' : '2026-11-05T09:58:00Z',
      })),
    }));
    renderInWorld(<HoustonView />, { data });
    const rows = within(await screen.findByRole('list', { name: 'Outbox' })).getAllByRole('listitem');
    expect(rows[0]).toHaveTextContent('received 3 Nov 09:58 GMT');
    expect(rows[1]).toHaveTextContent('received 09:58 GMT');
  });

  it('writes the escalation urgency and rules as words, falling back to the code', async () => {
    const data = mockWith((real) => ({
      ...real,
      summary: real.summary && {
        ...real.summary,
        escalation: { required: true, urgency: 'immediate', reasons: ['RULE_RED_FLAG_NEURO', 'RULE_FUTURE'] },
      },
    }));
    renderInWorld(<HoustonView />, { data });
    const text = (await screen.findByText(/^Required/)).textContent;
    expect(text).toMatch(/Required, urgency immediate/);
    expect(text).not.toMatch(/RULE_RED_FLAG_NEURO/);
    expect(text).toMatch(/RULE_FUTURE/);
  });

  it('titles a preflight summary L-30, in the summary and in the outbox', async () => {
    const data = mockWith((real) => ({
      ...real,
      summary: real.summary && { ...real.summary, missionDay: -30 },
      outbox: real.outbox.slice(0, 1).map((item) => ({ ...item, missionDay: -30 })),
    }));
    renderInWorld(<HoustonView />, { data });
    expect(await screen.findByRole('heading', { name: 'L-30 summary' })).toBeInTheDocument();
    expect(screen.getByRole('list', { name: 'Outbox' })).toHaveTextContent('L-30 summary');
    expect(screen.queryByText(/Day -30/)).not.toBeInTheDocument();
  });
});

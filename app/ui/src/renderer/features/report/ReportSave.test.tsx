import { act, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import type { DailyReportFields } from '../../../shared/api';
import { RealDataSource } from '../../data/real/RealDataSource';
import { DEFAULT_CONTEXT, FakeAsclepiusApi } from '../../test/fake-api';
import { renderInWorld } from '../../test/providers';
import { ReportView } from './ReportView';

const PAIN = 'How much does your back hurt right now?';
const STIFF = 'How stiff does your back feel?';
const QUALITY = 'How well did you sleep?';

async function setup(): Promise<FakeAsclepiusApi> {
  const api = new FakeAsclepiusApi();
  renderInWorld(<ReportView />, { api, data: new RealDataSource(api.views, api.reps, api), route: '/report' });
  await screen.findByRole('radiogroup', { name: PAIN });
  return api;
}

const choose = async (group: string, n: string): Promise<void> => {
  await userEvent.click(within(screen.getByRole('radiogroup', { name: group })).getByRole('radio', { name: n }));
};
const press = async (group: string, name: string): Promise<void> => {
  await userEvent.click(within(screen.getByRole('group', { name: group })).getByRole('button', { name }));
};
const minimal = async (): Promise<void> => {
  await choose(PAIN, '3');
  await choose(STIFF, '2');
  await userEvent.click(screen.getByRole('button', { name: 'None of these' }));
};
const save = (): HTMLElement => screen.getByRole('button', { name: 'Save daily report' });
const sent = (api: FakeAsclepiusApi): DailyReportFields => api.lastArg['reports.saveDaily'] as DailyReportFields;

describe('what the daily report sends', () => {
  it('maps every answer: places, sleep, warning symptoms and exercise', async () => {
    const api = await setup();
    await choose(PAIN, '4');
    await userEvent.click(screen.getByRole('button', { name: 'Lower back, left' }));
    await userEvent.click(screen.getByRole('button', { name: 'Neck, middle' }));
    await choose(STIFF, '2');
    await userEvent.click(screen.getByRole('button', { name: 'Leg weakness' }));
    await userEvent.click(screen.getByRole('button', { name: 'Fever with back pain' }));
    await press('How long did you sleep?', '7 h');
    await choose(QUALITY, '8');
    await press('Exercise device', 'CEVIS');
    await press('Exercise duration', '45 min');
    await userEvent.click(save());
    expect(sent(api)).toEqual({
      pain_nrs: 4,
      stiffness_nrs: 2,
      pain_locations: [
        { region: 'lumbar', side: 'left' },
        { region: 'cervical', side: 'midline' },
      ],
      sleep_hours: 7,
      sleep_quality_nrs: 8,
      red_flags: ['leg_weakness', 'fever_with_back_pain'],
      exercise: { modality: 'cevis', duration_min: 45 },
    });
  });

  it('"None of these" sends an empty red-flag list, not-sure sleep sends no hours, no exercise sends null', async () => {
    const api = await setup();
    await minimal();
    await press('How long did you sleep?', 'Not sure');
    await press('Exercise device', 'None yet');
    await userEvent.click(save());
    expect(sent(api)).toEqual({
      pain_nrs: 3,
      stiffness_nrs: 2,
      pain_locations: [],
      sleep_hours: null,
      sleep_quality_nrs: null,
      red_flags: [],
      exercise: null,
    });
  });

  it.each([
    ['ARED', 'ared'],
    ['T2 treadmill', 't2'],
  ] as const)('sends exercise device %s as %s', async (label, modality) => {
    const api = await setup();
    await minimal();
    await press('Exercise device', label);
    await press('Exercise duration', '30 min');
    await userEvent.click(save());
    expect(sent(api).exercise).toEqual({ modality, duration_min: 30 });
  });

  it('sends no places when the pain is zero, even if cells were marked before', async () => {
    const api = await setup();
    await choose(PAIN, '3');
    await userEvent.click(screen.getByRole('button', { name: 'Lower back, left' }));
    await choose(PAIN, '0');
    await choose(STIFF, '0');
    await userEvent.click(screen.getByRole('button', { name: 'None of these' }));
    await userEvent.click(save());
    expect(sent(api).pain_locations).toEqual([]);
  });

  it('asks for the exercise duration when a device is chosen without one, and then saves', async () => {
    const api = await setup();
    await minimal();
    await press('Exercise device', 'CEVIS');
    expect(save()).toBeDisabled();
    expect(screen.getByText('Still to answer: exercise duration.')).toBeInTheDocument();
    await press('Exercise duration', '15 min');
    expect(save()).toBeEnabled();
    await userEvent.click(save());
    expect(sent(api).exercise).toEqual({ modality: 'cevis', duration_min: 15 });
  });
});

describe('the outcome of saving', () => {
  it('says saved only once the backend took it, and not before', async () => {
    const api = await setup();
    let release: () => void = () => undefined;
    api.reports.saveDaily = () =>
      new Promise((resolve) => {
        release = () => {
          resolve({ ok: true, value: { reportId: 'r', assessmentLevel: null, assessmentSkipped: null, exercise: null } });
        };
      });
    await minimal();
    await userEvent.click(save());
    expect(screen.getByRole('button', { name: 'Saving' })).toBeDisabled();
    expect(screen.queryByText(/Daily report saved\./)).not.toBeInTheDocument();
    act(() => {
      release();
    });
    expect(await screen.findByText('Daily report saved. A new assessment is running.')).toBeInTheDocument();
    expect(save()).toBeEnabled();
  });

  it('a refusal on a field shows its message by that question and does not say saved', async () => {
    const api = await setup();
    api.forced.set('reports.saveDaily', { ok: false, reason: 'invalid', fields: [{ pointer: '/pain_nrs', message: 'must be at most 10' }] });
    await minimal();
    await userEvent.click(save());
    expect(await screen.findByText('must be at most 10')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('Check the marked answers.');
    expect(screen.queryByText(/Daily report saved\./)).not.toBeInTheDocument();
    expect(within(screen.getByRole('radiogroup', { name: PAIN })).getByRole('radio', { name: '3' })).toBeChecked();
  });

  it('maps a nested pointer to its question, and lists the ones that match no question', async () => {
    const api = await setup();
    api.forced.set('reports.saveDaily', {
      ok: false,
      reason: 'invalid',
      fields: [
        { pointer: '/pain_locations/0/region', message: 'unknown region' },
        { pointer: '/sleep/hours', message: 'too many hours' },
        { pointer: '/exercise', message: 'not accepted' },
      ],
    });
    await minimal();
    await userEvent.click(save());
    expect(await screen.findByText('unknown region')).toBeInTheDocument();
    expect(screen.getByText('too many hours')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('/exercise: not accepted');
  });

  it.each([
    ['unavailable', 'The monitor did not answer. Nothing was saved; your answers are still here, try again.'],
    ['locked', 'The app is locked. Unlock it, then save again.'],
    ['conflict', 'The monitor already holds a different report for this. Nothing was saved; reload the screen.'],
  ] as const)('on %s says nothing was saved, keeps the answers and lets the crew try again', async (reason, text) => {
    const api = await setup();
    api.forced.set('reports.saveDaily', { ok: false, reason });
    await minimal();
    await userEvent.click(save());
    expect(await screen.findByRole('alert')).toHaveTextContent(text);
    expect(screen.queryByText(/Daily report saved\./)).not.toBeInTheDocument();
    expect(save()).toBeEnabled();
    api.forced.delete('reports.saveDaily');
    await userEvent.click(save());
    expect(await screen.findByText('Daily report saved. A new assessment is running.')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('says the monitor did not answer when the bridge itself fails', async () => {
    const api = await setup();
    api.rejecting.add('reports.saveDaily');
    await minimal();
    await userEvent.click(save());
    expect(await screen.findByRole('alert')).toHaveTextContent('The monitor did not answer. Nothing was saved');
  });
});

describe('the exercise that the report could not store', () => {
  const SAVED = { reportId: 'r', assessmentLevel: null, assessmentSkipped: null } as const;
  const retry = (): HTMLElement => screen.getByRole('button', { name: 'Retry exercise' });
  const withExercise = async (): Promise<void> => {
    await minimal();
    await press('Exercise device', 'CEVIS');
    await press('Exercise duration', '45 min');
    await userEvent.click(save());
  };

  it('says the report was saved but the exercise was not, and the retry saves it and then says all is saved', async () => {
    const api = await setup();
    api.forced.set('reports.saveDaily', { ok: true, value: { ...SAVED, exercise: 'not_stored' } });
    await withExercise();
    expect(await screen.findByText(/Daily report saved, but your exercise was not/)).toBeInTheDocument();
    expect(screen.queryByText('Daily report saved. A new assessment is running.')).not.toBeInTheDocument();
    let release: () => void = () => undefined;
    api.reports.addExercise = () =>
      new Promise((resolve) => {
        release = () => {
          resolve({ ok: true, value: DEFAULT_CONTEXT });
        };
      });
    await userEvent.click(retry());
    expect(screen.getByRole('button', { name: 'Retrying' })).toBeDisabled();
    act(() => {
      release();
    });
    expect(await screen.findByText('Daily report saved. A new assessment is running.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Retry exercise' })).not.toBeInTheDocument();
  });

  it('sends the exercise that was filled in when retrying, and keeps offering the retry while it fails', async () => {
    const api = await setup();
    api.forced.set('reports.saveDaily', { ok: true, value: { ...SAVED, exercise: 'not_stored' } });
    api.forced.set('reports.addExercise', { ok: false, reason: 'unavailable' });
    await withExercise();
    await userEvent.click(await screen.findByRole('button', { name: 'Retry exercise' }));
    expect(api.lastArg['reports.addExercise']).toEqual({ modality: 'cevis', duration_min: 45 });
    expect(await screen.findByText(/Daily report saved, but your exercise was not/)).toBeInTheDocument();
    expect(retry()).toBeEnabled();
  });

  it("tells to set today's context in Profile when there is none", async () => {
    const api = await setup();
    api.forced.set('reports.saveDaily', { ok: true, value: { ...SAVED, exercise: 'no_context' } });
    await withExercise();
    expect(await screen.findByText(/no context for today. Set today's context in Profile/)).toBeInTheDocument();
    expect(retry()).toBeEnabled();
  });

  it('shows no retry when the exercise was stored or there was none', async () => {
    const api = await setup();
    api.forced.set('reports.saveDaily', { ok: true, value: { ...SAVED, exercise: 'stored' } });
    await withExercise();
    expect(await screen.findByText('Daily report saved. A new assessment is running.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Retry exercise' })).not.toBeInTheDocument();
  });
});

describe('the sleep quality without hours', () => {
  it('does not send, and asks for the hours on that question, until they are given', async () => {
    const api = await setup();
    await minimal();
    await choose(QUALITY, '6');
    await userEvent.click(save());
    expect(await screen.findByText('Say how long you slept to save how well you slept.')).toBeInTheDocument();
    expect(api.calls).not.toContain('reports.saveDaily');
    await press('How long did you sleep?', '7 h');
    expect(screen.queryByText('Say how long you slept to save how well you slept.')).not.toBeInTheDocument();
    await userEvent.click(save());
    expect(await screen.findByText('Daily report saved. A new assessment is running.')).toBeInTheDocument();
    expect(sent(api)).toMatchObject({ sleep_hours: 7, sleep_quality_nrs: 6 });
  });
});

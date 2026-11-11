import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { RealDataSource } from '../../data/real/RealDataSource';
import { FakeAsclepiusApi } from '../../test/fake-api';
import { renderInWorld } from '../../test/providers';
import { ProfileView } from './ProfileView';

function setup(api = new FakeAsclepiusApi()): FakeAsclepiusApi {
  renderInWorld(<ProfileView />, { api, data: new RealDataSource(api.views, api.reps, api), route: '/profile' });
  return api;
}

const profilePanel = (): HTMLElement => screen.getByRole('region', { name: 'Your profile' });
const contextPanel = (): HTMLElement => screen.getByRole('region', { name: 'Today’s context' });

describe('ProfileView', () => {
  it('prefills both forms from what is stored', async () => {
    setup();
    expect(await screen.findByLabelText('Date of birth')).toHaveValue('1988-04-12');
    expect(screen.getByLabelText('Standing height (cm)')).toHaveValue('171');
    expect(screen.getByLabelText('Sitting height (cm), optional')).toHaveValue('90');
    expect(screen.getByLabelText('Body mass (kg)')).toHaveValue('64.5');
    expect(screen.getByLabelText('Launch date, optional')).toHaveValue('2026-08-20');
    expect(screen.getByLabelText('Landing date, optional')).toHaveValue('');
    expect(within(profilePanel()).getByRole('button', { name: 'Female', pressed: true })).toBeInTheDocument();
    await screen.findByRole('region', { name: 'Today’s context' });
    expect(within(contextPanel()).getByRole('button', { name: 'Inflight', pressed: true })).toBeInTheDocument();
    expect(within(contextPanel()).getByRole('button', { name: 'Microgravity', pressed: true })).toBeInTheDocument();
    expect(screen.getByLabelText('Mission day, optional')).toHaveValue('78');
    expect(screen.queryByText('Saved')).not.toBeInTheDocument();
  });

  it('starts empty when nothing is stored, asks for what is missing, then sends what was typed', async () => {
    const api = new FakeAsclepiusApi();
    api.profileValue = null;
    api.contextValue = null;
    const user = userEvent.setup();
    setup(api);
    expect(await screen.findByLabelText('Date of birth')).toHaveValue('');
    await user.click(within(profilePanel()).getByRole('button', { name: 'Save profile' }));
    expect(api.calls).not.toContain('profile.update');
    expect(within(profilePanel()).getAllByText(/^Enter |^Choose /).length).toBeGreaterThanOrEqual(4);
    await user.type(screen.getByLabelText('Date of birth'), '1990-01-02');
    await user.click(within(profilePanel()).getByRole('button', { name: 'Male' }));
    await user.type(screen.getByLabelText('Standing height (cm)'), '180');
    await user.type(screen.getByLabelText('Body mass (kg)'), '80,5');
    await user.click(within(profilePanel()).getByRole('button', { name: 'Save profile' }));
    expect(await within(profilePanel()).findByText('Saved')).toBeInTheDocument();
    expect(api.lastProfileFields).toEqual({
      date_of_birth: '1990-01-02',
      sex_at_birth: 'male',
      launch_date: null,
      landing_date: null,
      height_standing_cm: 180,
      height_sitting_cm: null,
      body_mass_kg: 80.5,
    });
  });

  it('shows Saved only after the profile is stored, and drops it when the form changes', async () => {
    const api = setup();
    const user = userEvent.setup();
    const mass = await screen.findByLabelText('Body mass (kg)');
    await user.clear(mass);
    await user.type(mass, '66');
    await user.click(within(profilePanel()).getByRole('button', { name: 'Save profile' }));
    expect(await within(profilePanel()).findByText('Saved')).toBeInTheDocument();
    expect(api.lastProfileFields).toMatchObject({ body_mass_kg: 66, height_sitting_cm: 90, launch_date: '2026-08-20' });
    await user.type(mass, '1');
    expect(within(profilePanel()).queryByText('Saved')).not.toBeInTheDocument();
  });

  it('puts a refusal next to the field its pointer names, and unknown pointers in a general alert', async () => {
    const api = new FakeAsclepiusApi();
    api.profileWrite = {
      ok: false,
      reason: 'invalid',
      fields: [
        { pointer: '/anthropometry/body_mass_kg', message: 'must be at least 30' },
        { pointer: '/launch_date', message: 'not a date' },
        { pointer: '/gender_identity', message: 'unexpected' },
      ],
    };
    const user = userEvent.setup();
    setup(api);
    await screen.findByLabelText('Date of birth');
    await user.click(within(profilePanel()).getByRole('button', { name: 'Save profile' }));
    const mass = screen.getByLabelText('Body mass (kg)');
    expect(mass).toHaveAccessibleDescription('must be at least 30');
    expect(mass).toBeInvalid();
    expect(screen.getByLabelText('Launch date, optional')).toHaveAccessibleDescription('not a date');
    expect(screen.getByLabelText('Date of birth')).toBeValid();
    expect(within(profilePanel()).getByRole('alert')).toHaveTextContent('/gender_identity: unexpected');
    expect(within(profilePanel()).queryByText('Saved')).not.toBeInTheDocument();
  });

  it.each([
    ['unavailable', 'The monitor did not answer. Nothing was saved; your entries are still here, try again.'],
    ['locked', 'The app is locked. Unlock it, then save again.'],
    ['conflict', 'The monitor already holds different content for this. Nothing was saved; reload the screen.'],
  ] as const)('says %s in general, keeps the entries and never says Saved', async (reason, text) => {
    const api = new FakeAsclepiusApi();
    api.profileWrite = { ok: false, reason };
    const user = userEvent.setup();
    setup(api);
    const mass = await screen.findByLabelText('Body mass (kg)');
    await user.clear(mass);
    await user.type(mass, '70');
    await user.click(within(profilePanel()).getByRole('button', { name: 'Save profile' }));
    expect(await within(profilePanel()).findByRole('alert')).toHaveTextContent(text);
    expect(screen.getByLabelText('Body mass (kg)')).toHaveValue('70');
    expect(screen.queryByText('Saved')).not.toBeInTheDocument();
  });

  it('saves today’s context on its own, mission day left out when empty', async () => {
    const api = setup();
    const user = userEvent.setup();
    await user.clear(await screen.findByLabelText('Mission day, optional'));
    await user.click(within(contextPanel()).getByRole('button', { name: 'Preflight' }));
    await user.click(within(contextPanel()).getByRole('button', { name: 'Earth gravity (1 g)' }));
    await user.click(within(contextPanel()).getByRole('button', { name: 'Save context' }));
    expect(await within(contextPanel()).findByText('Saved')).toBeInTheDocument();
    expect(api.lastContextFields).toEqual({ phase: 'preflight', gravity_environment: 'earth_1g', mission_day: null });
    expect(api.calls).not.toContain('profile.update');
    expect(within(profilePanel()).queryByText('Saved')).not.toBeInTheDocument();
  });

  it('refuses day 0 locally and shows context refusals by field', async () => {
    const api = new FakeAsclepiusApi();
    const user = userEvent.setup();
    setup(api);
    const day = await screen.findByLabelText('Mission day, optional');
    await user.clear(day);
    await user.type(day, '0');
    await user.click(within(contextPanel()).getByRole('button', { name: 'Save context' }));
    expect(day).toHaveAccessibleDescription('Use a whole number other than 0: launch day is 1, the day before is -1.');
    expect(api.calls).not.toContain('dailyContext.updateToday');
    api.contextWrite = { ok: false, reason: 'invalid', fields: [{ pointer: '/mission_day', message: 'too far' }] };
    await user.clear(day);
    await user.type(day, '5');
    await user.click(within(contextPanel()).getByRole('button', { name: 'Save context' }));
    expect(await screen.findByText('too far')).toBeInTheDocument();
    api.contextWrite = { ok: false, reason: 'unavailable' };
    await user.click(within(contextPanel()).getByRole('button', { name: 'Save context' }));
    expect(await within(contextPanel()).findByRole('alert')).toHaveTextContent('Nothing was saved');
    expect(within(contextPanel()).queryByText('Saved')).not.toBeInTheDocument();
  });

  it('says so when the profile cannot be read, and offers no form', async () => {
    const api = new FakeAsclepiusApi();
    api.readErrors = { profile: 'unavailable' };
    setup(api);
    expect(await screen.findByTestId('no-monitor')).toBeInTheDocument();
    expect(screen.queryByLabelText('Date of birth')).not.toBeInTheDocument();
  });
});

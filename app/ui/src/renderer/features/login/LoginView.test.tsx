import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes } from 'react-router';
import { describe, expect, it } from 'vitest';

import { FakeAsclepiusApi } from '../../test/fake-api';
import { renderInWorld } from '../../test/providers';
import { LoginView } from './LoginView';

function setup(api = new FakeAsclepiusApi()) {
  const world = renderInWorld(
    <Routes>
      <Route path="/login" element={<LoginView />} />
      <Route path="/today" element={<p>today page</p>} />
    </Routes>,
    { api, route: '/login', signedIn: false },
  );
  return { ...world, user: userEvent.setup() };
}

type User = ReturnType<typeof userEvent.setup>;

async function pressPin(user: User, digits: string): Promise<void> {
  for (const d of digits) await user.click(screen.getByRole('button', { name: d }));
}

describe('LoginView', () => {
  it('shows the product line, side copy and facts from the strip', async () => {
    setup();
    expect(screen.getByText('Asclepius Orbit, on-board spine monitoring')).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 1, name: 'Who is using this tablet?' })).toBeInTheDocument();
    expect(await screen.findByText('13:40')).toBeInTheDocument();
    expect(screen.getByText(/mission day 87/)).toBeInTheDocument();
    expect(screen.getByText('Down')).toBeInTheDocument();
    expect(screen.getByText(/back in 12 min/)).toBeInTheDocument();
  });

  it('lists crew from the bridge with initials and harness', async () => {
    setup();
    const tile = await screen.findByRole('button', { name: /Elena Ruiz/ });
    expect(within(tile).getByText('ER')).toBeInTheDocument();
    expect(within(tile).getByText('Harness wb-01')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /New crew member/ })).toBeInTheDocument();
  });

  it('renders a hostile name as text, never as markup', async () => {
    const api = new FakeAsclepiusApi();
    api.crewMembers.push({ id: 'AST-03', name: '<img src=x onerror=alert(1)>', harnessId: null });
    const { container } = setup(api);
    expect(await screen.findByText('<img src=x onerror=alert(1)>')).toBeInTheDocument();
    expect(container.querySelector('img')).toBeNull();
  });

  it('shows a clear message when the crew list cannot be read', async () => {
    const api = new FakeAsclepiusApi();
    api.crew.list = () => Promise.reject(new Error('down'));
    setup(api);
    expect(await screen.findByText('The crew list could not be read. Try again in a moment.')).toBeInTheDocument();
  });

  it('signs in with the right PIN and goes to Today; digits are never shown', async () => {
    const { user, api } = setup();
    await user.click(await screen.findByRole('button', { name: /Elena Ruiz/ }));
    expect(screen.getByText('PIN for Elena Ruiz')).toBeInTheDocument();
    await pressPin(user, '123');
    expect(screen.queryByText('123')).toBeNull();
    expect(screen.getByText('3 of 4 digits entered')).toBeInTheDocument();
    await pressPin(user, '4');
    expect(await screen.findByText('today page')).toBeInTheDocument();
    expect(api.calls).toContain('auth.verifyPin');
  });

  it('accepts the physical keyboard and Backspace', async () => {
    const { user } = setup();
    await user.click(await screen.findByRole('button', { name: /Elena Ruiz/ }));
    await user.keyboard('129{Backspace}34');
    expect(await screen.findByText('today page')).toBeInTheDocument();
  });

  it('says how many tries are left after a wrong PIN and clears the entry', async () => {
    const { user } = setup();
    await user.click(await screen.findByRole('button', { name: /Elena Ruiz/ }));
    await pressPin(user, '0000');
    expect(await screen.findByText('That PIN does not match. 2 tries left.')).toBeInTheDocument();
    expect(screen.getByText('0 of 4 digits entered')).toBeInTheDocument();
    await pressPin(user, '0000');
    expect(await screen.findByText('That PIN does not match. 1 try left.')).toBeInTheDocument();
  });

  it('shows the lockout with minutes rounded up', async () => {
    const api = new FakeAsclepiusApi();
    api.auth.verifyPin = () => Promise.resolve({ ok: false, reason: 'locked', retryAfterMs: 4 * 60_000 + 1 });
    const { user } = setup(api);
    await user.click(await screen.findByRole('button', { name: /Elena Ruiz/ }));
    await pressPin(user, '0000');
    expect(await screen.findByText('Too many tries. Wait 5 min or ask the crew medical officer to reset your PIN.')).toBeInTheDocument();
  });

  it.each([
    ['unavailable', 'The tablet could not reach its secure store. Try again, or ask the crew medical officer.'],
    ['unknown_crew', 'Something went wrong. Pick your name and try again.'],
  ] as const)('maps %s to a generic message', async (reason, text) => {
    const api = new FakeAsclepiusApi();
    api.auth.verifyPin = () => Promise.resolve({ ok: false, reason });
    const { user } = setup(api);
    await user.click(await screen.findByRole('button', { name: /Elena Ruiz/ }));
    await pressPin(user, '0000');
    expect(await screen.findByText(text)).toBeInTheDocument();
  });

  it('Delete removes the last digit and Enter with fewer than 4 digits does nothing', async () => {
    const { user } = setup();
    await user.click(await screen.findByRole('button', { name: /Elena Ruiz/ }));
    await pressPin(user, '12');
    await user.click(screen.getByRole('button', { name: 'Delete last digit' }));
    expect(screen.getByText('1 of 4 digits entered')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Enter' }));
    expect(screen.getByText('1 of 4 digits entered')).toBeInTheDocument();
  });

  it('opens the Urgent dialog without a login', async () => {
    const { user } = setup();
    await user.click(screen.getByRole('button', { name: 'Urgent symptom, no login needed' }));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
  });

  describe('new crew member', () => {
    async function openForm(user: User): Promise<void> {
      await user.click(await screen.findByRole('button', { name: /New crew member/ }));
    }

    it('shows the next crew code and validates the name client-side', async () => {
      const { user } = setup();
      await openForm(user);
      expect(await screen.findByText('AST-03')).toBeInTheDocument();
      await user.type(screen.getByLabelText('Your name'), ' A ');
      await user.click(screen.getByRole('button', { name: 'Continue' }));
      expect(screen.getByText('Write your name, at least 2 letters.')).toBeInTheDocument();
      await user.clear(screen.getByLabelText('Your name'));
      await user.type(screen.getByLabelText('Your name'), 'elena   ruiz');
      await user.click(screen.getByRole('button', { name: 'Continue' }));
      expect(screen.getByText('elena ruiz is already on this tablet. Pick it above.')).toBeInTheDocument();
    });

    it('creates the member after the PIN is chosen twice and signs in', async () => {
      const { user, api } = setup();
      await openForm(user);
      await user.type(screen.getByLabelText('Your name'), '  Ana   García ');
      await user.click(screen.getByRole('button', { name: 'Continue' }));
      expect(screen.getByText('Choose a PIN for Ana García')).toBeInTheDocument();
      await pressPin(user, '4321');
      expect(screen.getByText('Enter the PIN again')).toBeInTheDocument();
      await pressPin(user, '4321');
      expect(await screen.findByText('today page')).toBeInTheDocument();
      expect(api.crewMembers.at(-1)?.name).toBe('Ana García');
      expect(api.pins.get('AST-03')).toBe('4321');
    });

    it('asks to choose again when the two PINs differ', async () => {
      const { user, api } = setup();
      await openForm(user);
      await user.type(screen.getByLabelText('Your name'), 'Ana García');
      await user.click(screen.getByRole('button', { name: 'Continue' }));
      await pressPin(user, '4321');
      await pressPin(user, '1111');
      expect(screen.getByText('The two PINs are different. Choose it again.')).toBeInTheDocument();
      expect(screen.getByText('Choose a PIN for Ana García')).toBeInTheDocument();
      expect(api.calls).not.toContain('crew.create');
    });

    it('Back to your name keeps the typed name; Cancel returns to the tiles', async () => {
      const { user } = setup();
      await openForm(user);
      await user.type(screen.getByLabelText('Your name'), 'Ana García');
      await user.click(screen.getByRole('button', { name: 'Continue' }));
      await user.click(screen.getByRole('button', { name: 'Back to your name' }));
      expect(screen.getByLabelText('Your name')).toHaveValue('Ana García');
      await user.click(screen.getByRole('button', { name: 'Cancel' }));
      expect(screen.getByRole('button', { name: /Elena Ruiz/ })).toBeInTheDocument();
    });

    it.each([
      ['name_too_short', 'Write your name, at least 2 letters.', 'name'],
      ['name_taken', 'Ana García is already on this tablet. Pick it above.', 'name'],
      ['invalid_pin', 'That PIN cannot be used. Choose 4 digits.', 'pin'],
      ['unavailable', 'The tablet could not reach its secure store. Try again, or ask the crew medical officer.', 'pin'],
    ] as const)('maps the create refusal %s', async (reason, text, step) => {
      const api = new FakeAsclepiusApi();
      api.crew.create = () => Promise.resolve({ ok: false, reason });
      const { user } = setup(api);
      await openForm(user);
      await user.type(screen.getByLabelText('Your name'), 'Ana García');
      await user.click(screen.getByRole('button', { name: 'Continue' }));
      await pressPin(user, '43214321');
      expect(await screen.findByText(text)).toBeInTheDocument();
      if (step === 'name') expect(screen.getByLabelText('Your name')).toBeInTheDocument();
      else expect(screen.getByText('Choose a PIN for Ana García')).toBeInTheDocument();
    });
  });
});

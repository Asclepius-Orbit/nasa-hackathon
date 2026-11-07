import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { FakeAsclepiusApi } from '../../test/fake-api';
import { renderInWorld } from '../../test/providers';
import { SettingsView } from './SettingsView';

function group(name: string): HTMLElement {
  return screen.getByRole('group', { name });
}

describe('SettingsView', () => {
  it('shows the stored values as pressed', async () => {
    const api = new FakeAsclepiusApi();
    api.settingsValue = { lengthUnit: 'in', timeFormat: 'gmt_met', textSize: 'standard', voiceCommands: false, footPedal: true };
    renderInWorld(<SettingsView />, { api });
    expect(await within(group('Length')).findByRole('button', { name: 'inches', pressed: true })).toBeInTheDocument();
    expect(within(group('Length')).getByRole('button', { name: 'mm' })).toHaveAttribute('aria-pressed', 'false');
    expect(within(group('Time')).getByRole('button', { name: 'GMT and mission elapsed time' })).toHaveAttribute('aria-pressed', 'true');
    expect(within(group('Text size')).getByRole('button', { name: 'Standard' })).toHaveAttribute('aria-pressed', 'true');
    expect(within(group('Voice commands')).getByRole('button', { name: 'Off' })).toHaveAttribute('aria-pressed', 'true');
    expect(within(group('Foot pedal')).getByRole('button', { name: 'Paired' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText('“stop, pain” always asks to confirm')).toBeInTheDocument();
  });

  it.each([
    ['Length', 'inches', { lengthUnit: 'in' }],
    ['Time', 'GMT and mission elapsed time', { timeFormat: 'gmt_met' }],
    ['Text size', 'Standard', { textSize: 'standard' }],
    ['Voice commands', 'Off', { voiceCommands: false }],
    ['Foot pedal', 'Paired', { footPedal: true }],
  ] as const)('%s: choosing %s saves through the bridge', async (label, choice, patch) => {
    const api = new FakeAsclepiusApi();
    const user = userEvent.setup();
    renderInWorld(<SettingsView />, { api });
    await user.click(await within(group(label)).findByRole('button', { name: choice }));
    expect(await within(group(label)).findByRole('button', { name: choice })).toHaveAttribute('aria-pressed', 'true');
    expect(api.settingsValue).toMatchObject(patch);
  });

  it('says so when a setting cannot be saved', async () => {
    const api = new FakeAsclepiusApi();
    api.settings.update = () => Promise.reject(new Error('disk'));
    const user = userEvent.setup();
    renderInWorld(<SettingsView />, { api });
    await user.click(await within(group('Length')).findByRole('button', { name: 'inches' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('That setting could not be saved. Try again.');
  });
});

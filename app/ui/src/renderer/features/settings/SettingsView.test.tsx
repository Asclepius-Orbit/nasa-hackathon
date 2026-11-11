import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useLocation } from 'react-router';
import { describe, expect, it } from 'vitest';

import type { DeviceStatus } from '../../../shared/api';
import { RealDataSource } from '../../data/real/RealDataSource';
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

const device = (id: string, extra: Partial<DeviceStatus> = {}): DeviceStatus => ({
  schema_version: '1.0.0',
  astronaut_id: 'AST-01',
  device_id: id,
  timestamp: '2026-11-05T13:35:00Z',
  status: 'ok',
  source: 'device',
  battery_pct: 80,
  ...extra,
});

function Where(): React.JSX.Element {
  return <p data-testid="where">{useLocation().pathname}</p>;
}

function harnessWorld(api: FakeAsclepiusApi): void {
  renderInWorld(
    <>
      <SettingsView />
      <Where />
    </>,
    { api, data: new RealDataSource(api.views, api.reps, api), route: '/settings' },
  );
}

describe('SettingsView harness', () => {
  it('links to the profile', async () => {
    harnessWorld(new FakeAsclepiusApi());
    await userEvent.click(screen.getByRole('button', { name: 'Open profile' }));
    expect(screen.getByTestId('where')).toHaveTextContent('/profile');
  });

  it('shows the current pairing and every device seen: id, battery, last seen, degraded', async () => {
    const api = new FakeAsclepiusApi();
    api.devicesValue = [device('wb-01', { battery_pct: 18, status: 'degraded' }), device('wb-02', { timestamp: '2026-11-04T09:03:00Z' })];
    harnessWorld(api);
    const harness = screen.getByRole('region', { name: 'Harness' });
    expect(await within(harness).findByText('Paired with wb-01')).toBeInTheDocument();
    const first = within(harness).getByRole('listitem', { name: 'wb-01' });
    expect(first).toHaveTextContent('Battery 18 %');
    expect(first).toHaveTextContent('Last seen 13:35');
    expect(first).toHaveTextContent('Degraded');
    expect(within(first).getByText('Paired')).toBeInTheDocument();
    const second = within(harness).getByRole('listitem', { name: 'wb-02' });
    expect(second).toHaveTextContent('Last seen 4 Nov 09:03');
    expect(second).not.toHaveTextContent('Degraded');
    expect(within(second).getByRole('button', { name: 'Pair wb-02' })).toBeInTheDocument();
  });

  it('pairs another harness through the bridge and shows it as paired', async () => {
    const api = new FakeAsclepiusApi();
    api.devicesValue = [device('wb-01'), device('wb-02')];
    const user = userEvent.setup();
    harnessWorld(api);
    await user.click(await screen.findByRole('button', { name: 'Pair wb-02' }));
    expect(await screen.findByText('Paired with wb-02')).toBeInTheDocument();
    expect(api.lastHarnessId).toBe('wb-02');
    expect(screen.getByRole('button', { name: 'Pair wb-01' })).toBeInTheDocument();
  });

  it('unpairs', async () => {
    const api = new FakeAsclepiusApi();
    api.devicesValue = [device('wb-01')];
    const user = userEvent.setup();
    harnessWorld(api);
    await user.click(await screen.findByRole('button', { name: 'Unpair' }));
    expect(await screen.findByText('Not paired yet')).toBeInTheDocument();
    expect(api.lastHarnessId).toBeNull();
    expect(screen.getByRole('button', { name: 'Pair wb-01' })).toBeInTheDocument();
  });

  it.each([
    ['unavailable', 'The pairing could not be saved on this device. Nothing changed; try again.'],
    ['locked', 'The app is locked. Unlock it, then try again.'],
  ] as const)('says %s and keeps the old pairing', async (reason, text) => {
    const api = new FakeAsclepiusApi();
    api.devicesValue = [device('wb-01'), device('wb-02')];
    api.harnessFailure = reason;
    const user = userEvent.setup();
    harnessWorld(api);
    await user.click(await screen.findByRole('button', { name: 'Pair wb-02' }));
    expect(await screen.findByText(text)).toBeInTheDocument();
    expect(screen.getByText('Paired with wb-01')).toBeInTheDocument();
  });

  it('explains what to do when no harness was seen', async () => {
    harnessWorld(new FakeAsclepiusApi());
    expect(await screen.findByText(/No harness has reported yet/)).toHaveTextContent('Switch the harness on');
  });

  it('shows no pairing before a device is chosen for a member without a harness', async () => {
    const api = new FakeAsclepiusApi();
    api.crewMembers[0] = { id: 'AST-01', name: 'Elena Ruiz', harnessId: null };
    api.devicesValue = [device('wb-02')];
    harnessWorld(api);
    expect(await screen.findByText('Not paired yet')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Unpair' })).not.toBeInTheDocument();
  });

  it('says so when the devices cannot be listed', async () => {
    const api = new FakeAsclepiusApi();
    api.readErrors = { devices: 'unavailable' };
    harnessWorld(api);
    expect(await screen.findByTestId('no-monitor')).toBeInTheDocument();
  });
});

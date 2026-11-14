import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { CordProtocolsView } from '../../../shared/api';
import { toCordProtocols } from '../../data/real/map-views';
import { example } from '../../test/contract-examples';
import { CordSessions } from './CordSessions';

describe('CordSessions estimate', () => {
  it('names an unknown reason code instead of passing it off as an engine outage', () => {
    const cord = toCordProtocols(example<CordProtocolsView>('ui-cord-protocols'));
    const [straight, flexion] = cord.postures;
    if (straight === undefined || flexion === undefined) throw new Error('both postures expected');
    render(<CordSessions cord={{ ...cord, postures: [straight, { ...flexion, estimateUnavailable: 'ESTIMATE_FUTURE' }] }} />);
    expect(screen.getByText('No estimate: ESTIMATE_FUTURE')).toBeInTheDocument();
    expect(screen.queryByText(/the calculation is not available right now/)).not.toBeInTheDocument();
  });
});

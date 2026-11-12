/** Devices that published a status for the unlocked astronaut, to pair a harness. Rejects with a `ViewsError`. */
import type { DeviceStatus } from '../../shared/api';
import type { AuthService } from '../auth/auth-service';
import type { RecordsClient } from '../views/records-client';
import { requireUnlocked } from './unlocked';

export interface DevicesService {
  list(): Promise<DeviceStatus[]>;
}

export function createDevicesService({
  auth,
  records,
}: {
  auth: Pick<AuthService, 'current'>;
  records: Pick<RecordsClient, 'devices'>;
}): DevicesService {
  return { list: async () => records.devices(await requireUnlocked(auth)) };
}

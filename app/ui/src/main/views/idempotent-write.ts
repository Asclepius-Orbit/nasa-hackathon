import type { WriteResult } from '../../shared/api';
import type { WriteClient, WriteRequest } from './write-client';

/** Prepare one logical POST in main. Reusing the returned function retries the identical body and identifier. */
export function prepareIdempotentWrite<T>(
  writer: Pick<WriteClient, 'send'>,
  request: Omit<WriteRequest<T>, 'method' | 'body'>,
  idField: 'report_id' | 'contact_id',
  body: Record<string, unknown>,
  newId: () => string = () => crypto.randomUUID(),
): () => Promise<WriteResult<T>> {
  // Capture the full JSON payload once: edits to the caller's form cannot change a retry.
  const payload = JSON.stringify({ ...body, [idField]: newId() });
  return () => writer.send({ ...request, method: 'POST', body: JSON.parse(payload) as unknown });
}

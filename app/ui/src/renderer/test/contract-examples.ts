/** The valid examples of the app view schemas, read from the contracts folder so the tests use the exact payloads the backend promises. */
const files = import.meta.glob<string>('../../../../../spine_monitor/contracts/examples/valid/app/ui-*.json', {
  eager: true,
  query: '?raw',
  import: 'default',
});

/** `example('ui-today')` parses `ui-today.json`; `example('ui-session.spine-check')` the variant. */
// eslint-disable-next-line @typescript-eslint/no-unnecessary-type-parameters -- the caller names the contract type of the example it loads
export function example<T>(name: string): T {
  const entry = Object.entries(files).find(([path]) => path.endsWith(`/${name}.json`));
  if (entry === undefined) throw new Error(`No contract example ${name}`);
  return JSON.parse(entry[1]) as T;
}

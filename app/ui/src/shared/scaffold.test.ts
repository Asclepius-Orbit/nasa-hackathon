// Scaffold sanity test: proves the node test project runs. Replace with real tests.
import { expect, it } from 'vitest';

it('scaffold sanity: node project executes', () => {
  expect([1, 2].map((n) => n * 2)).toEqual([2, 4]);
});

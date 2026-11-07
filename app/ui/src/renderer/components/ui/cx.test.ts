import { expect, it } from 'vitest';

import { cx } from './cx';

it('joins the class names that exist', () => {
  expect(cx('a', undefined, false, '', 'b')).toBe('a b');
  expect(cx(undefined)).toBe('');
});

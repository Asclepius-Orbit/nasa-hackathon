/** Joins class names, skipping empty ones (CSS-module lookups are typed `string | undefined`). */
export function cx(...parts: (string | false | undefined)[]): string {
  return parts.filter((p): p is string => typeof p === 'string' && p !== '').join(' ');
}

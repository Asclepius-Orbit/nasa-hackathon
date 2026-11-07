/**
 * Content-Security-Policy sent as a response header. Must equal the <meta> CSP in src/renderer/index.html
 * (a test checks it); in dev it applies the same relaxations as the `devCsp` plugin in electron.vite.config.ts,
 * otherwise the stricter header would cancel them and break HMR.
 */
export const PROD_CSP =
  "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; " +
  "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'";

export function cspFor(dev: boolean): string {
  if (!dev) return PROD_CSP;
  return PROD_CSP.replace("script-src 'self'", "script-src 'self' 'unsafe-inline'")
    .replace("style-src 'self'", "style-src 'self' 'unsafe-inline'")
    .replace("connect-src 'self'", "connect-src 'self' ws://localhost:* http://localhost:*");
}

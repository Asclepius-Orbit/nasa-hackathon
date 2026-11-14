/** The session screen, with or without a query (a cord task card opens `/session?protocol=...`). */
export const SESSION_ROUTE = '/session';

export const isSessionRoute = (route: string | null | undefined): boolean =>
  route !== null && route !== undefined && (route === SESSION_ROUTE || route.startsWith(`${SESSION_ROUTE}?`));

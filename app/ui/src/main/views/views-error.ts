import type { ViewsErrorCode } from '../../shared/api';

/** A failure of a view request, one of the codes the renderer is allowed to tell apart. */
export class ViewsError extends Error {
  readonly code: ViewsErrorCode;

  /** `cause` is the underlying failure (network error, bad body...); it is logged in main, never sent to the renderer. */
  constructor(code: ViewsErrorCode, options?: { cause?: unknown }) {
    super(code, options);
    this.code = code;
  }
}

import { createContext, useCallback, useContext, useMemo, useState } from 'react';

import type { RedFlagCode, EscapeCode } from '../../data/types';

export interface UrgentApi {
  /** Opens the Urgent dialog; `preselected` marks symptoms already chosen (e.g. from the report). Works with no session. */
  open: (preselected?: readonly (RedFlagCode | EscapeCode)[]) => void;
  close: () => void;
  isOpen: boolean;
  /** Symptoms to pre-mark when the dialog opened. */
  preselected: readonly (RedFlagCode | EscapeCode)[];
}

const UrgentContext = createContext<UrgentApi | null>(null);

/** Owns the open state of the Urgent dialog. Mount `<UrgentDialog />` once beside it, above the router. */
export function UrgentProvider({ children }: { children: React.ReactNode }): React.JSX.Element {
  const [isOpen, setIsOpen] = useState(false);
  const [preselected, setPreselected] = useState<readonly (RedFlagCode | EscapeCode)[]>([]);
  const open = useCallback((codes?: readonly (RedFlagCode | EscapeCode)[]) => {
    setPreselected(Array.isArray(codes) ? codes : []);
    setIsOpen(true);
  }, []);
  const close = useCallback(() => {
    setIsOpen(false);
  }, []);
  const value = useMemo<UrgentApi>(() => ({ open, close, isOpen, preselected }), [open, close, isOpen, preselected]);
  return <UrgentContext value={value}>{children}</UrgentContext>;
}

export function useUrgent(): UrgentApi {
  const value = useContext(UrgentContext);
  if (value === null) throw new Error('useUrgent must be used inside UrgentProvider');
  return value;
}

import { useRef, useState } from 'react';

import type { AstronautProfile, DailyContext, DailyContextFields, ProfileFields, WriteResult } from '../../../shared/api';
import { LoadFailure } from '../../components/layout/NoMonitor';
import { Alert } from '../../components/ui/Alert';
import { Button } from '../../components/ui/Button';
import { Panel } from '../../components/ui/Panel';
import { useData } from '../../data/DataContext';
import type { DataSource } from '../../data/DataSource';
import { useLoad } from '../../data/useLoad';
import { profileText as t } from '../../strings/profile';
import { ChoiceField, NO_ERRORS, parseNumber, sortFieldErrors, TextField, type FormErrors } from './fields';
import styles from './Profile.module.css';

/** `null` is a real answer here ("nothing stored"), so the loaded value is wrapped: `useLoad` uses a bare `null` for "not loaded". */
const selectProfile = async (d: DataSource): Promise<{ stored: AstronautProfile | null }> => ({ stored: await d.getProfile() });
const selectContext = async (d: DataSource): Promise<{ stored: DailyContext | null }> => ({ stored: await d.getTodayContext() });

const PROFILE_POINTERS = [
  '/date_of_birth',
  '/sex_at_birth',
  '/launch_date',
  '/landing_date',
  '/anthropometry/height_standing_cm',
  '/anthropometry/height_sitting_cm',
  '/anthropometry/body_mass_kg',
] as const;
const CONTEXT_POINTERS = ['/phase', '/gravity_environment', '/mission_day'] as const;

const options = <V extends string>(labels: Record<V, string>): { value: V; label: string }[] =>
  (Object.keys(labels) as V[]).map((value) => ({ value, label: labels[value] }));

interface SaveState {
  status: 'idle' | 'saving' | 'saved';
  errors: FormErrors;
  /** Why nothing was stored when no field is to blame. */
  failure: string | null;
}
const IDLE: SaveState = { status: 'idle', errors: NO_ERRORS, failure: null };

/**
 * One form's save: "Saved" only after `ok: true`; a refusal keeps the entries and shows its reason (by field when its
 * pointer is one of `known`, otherwise in the general alert). Any change to the form clears the previous outcome.
 * While a send is in flight nothing is cleared and a second send is ignored: the form is locked (see `saving`), so the
 * answer always belongs to the values on screen and writes cannot finish out of order.
 */
function useSave<T>(known: readonly string[]): {
  state: SaveState;
  clear: () => void;
  reject: (errors: FormErrors) => void;
  run: (send: () => Promise<WriteResult<T>>) => void;
} {
  const [state, setState] = useState<SaveState>(IDLE);
  /** A ref, not `state`: two submits in the same tick would both see a stale `idle`. */
  const inFlight = useRef(false);
  const run = (send: () => Promise<WriteResult<T>>): void => {
    if (inFlight.current) return;
    inFlight.current = true;
    setState({ ...IDLE, status: 'saving' });
    send().then(
      (result) => {
        inFlight.current = false;
        if (result.ok) setState({ ...IDLE, status: 'saved' });
        else if (result.reason === 'invalid') setState({ ...IDLE, errors: sortFieldErrors(result.fields, known) });
        else setState({ ...IDLE, failure: t.failure[result.reason] });
      },
      () => {
        inFlight.current = false;
        setState({ ...IDLE, failure: t.failure.unavailable });
      },
    );
  };
  return {
    state,
    clear: () => {
      if (!inFlight.current) setState(IDLE);
    },
    reject: (errors) => {
      setState({ ...IDLE, errors });
    },
    run,
  };
}

function SaveBar({ label, state }: { label: string; state: SaveState }): React.JSX.Element {
  const unmatched = state.errors.general;
  const marked = Object.keys(state.errors.byPointer).length > 0;
  return (
    <>
      {(state.failure !== null || unmatched.length > 0 || marked) && (
        <div className={styles.summary}>
          <Alert variant="urgent">
            {state.failure ?? (marked ? t.fixMarked : null)}
            {unmatched.map((line) => (
              <span key={line} style={{ display: 'block' }}>
                {line}
              </span>
            ))}
          </Alert>
        </div>
      )}
      <div className={styles.actions}>
        <Button type="submit" disabled={state.status === 'saving'}>
          {state.status === 'saving' ? t.saving : label}
        </Button>
        <p className={styles.saved} role="status">
          {state.status === 'saved' ? t.saved : ''}
        </p>
      </div>
    </>
  );
}

const text = (n: number | undefined): string => (n === undefined ? '' : String(n));

/** Reads the form into the fields to send, or says what is wrong with each entry (nothing is sent then). */
function readProfile(form: ProfileDraft): ProfileFields | FormErrors {
  const byPointer: Record<string, string> = {};
  const standing = parseNumber(form.standing);
  const mass = parseNumber(form.mass);
  const sitting = form.sitting.trim() === '' ? null : parseNumber(form.sitting);
  if (form.dateOfBirth === '') byPointer['/date_of_birth'] = t.needDateOfBirth;
  if (form.sex === null) byPointer['/sex_at_birth'] = t.needSex;
  if (standing === null) byPointer['/anthropometry/height_standing_cm'] = t.needStanding;
  if (mass === null) byPointer['/anthropometry/body_mass_kg'] = t.needMass;
  if (form.sitting.trim() !== '' && sitting === null) byPointer['/anthropometry/height_sitting_cm'] = t.needSitting;
  if (form.dateOfBirth === '' || form.sex === null || standing === null || mass === null || Object.keys(byPointer).length > 0) {
    return { byPointer, general: [] };
  }
  return {
    date_of_birth: form.dateOfBirth,
    sex_at_birth: form.sex,
    launch_date: form.launch === '' ? null : form.launch,
    landing_date: form.landing === '' ? null : form.landing,
    height_standing_cm: standing,
    height_sitting_cm: sitting,
    body_mass_kg: mass,
  };
}

interface ProfileDraft {
  dateOfBirth: string;
  sex: AstronautProfile['sex_at_birth'] | null;
  standing: string;
  sitting: string;
  mass: string;
  launch: string;
  landing: string;
}

function ProfileForm({ stored, source }: { stored: AstronautProfile | null; source: DataSource }): React.JSX.Element {
  const [form, setForm] = useState<ProfileDraft>({
    dateOfBirth: stored?.date_of_birth ?? '',
    sex: stored?.sex_at_birth ?? null,
    standing: text(stored?.anthropometry.height_standing_cm),
    sitting: text(stored?.anthropometry.height_sitting_cm),
    mass: text(stored?.anthropometry.body_mass_kg),
    launch: stored?.launch_date ?? '',
    landing: stored?.landing_date ?? '',
  });
  const save = useSave<AstronautProfile>(PROFILE_POINTERS);
  const set =
    <K extends keyof ProfileDraft>(key: K) =>
    (value: ProfileDraft[K]): void => {
      save.clear();
      setForm((previous) => ({ ...previous, [key]: value }));
    };
  const error = (pointer: (typeof PROFILE_POINTERS)[number]): string | undefined => save.state.errors.byPointer[pointer];

  return (
    <Panel as="section" aria-labelledby="profile-heading" className={styles.panel}>
      <h2 id="profile-heading">{t.profileHeading}</h2>
      <form
        className={styles.form}
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          const read = readProfile(form);
          if ('byPointer' in read) save.reject(read);
          else save.run(() => source.saveProfile(read));
        }}
      >
        <fieldset className={styles.fields} disabled={save.state.status === 'saving'}>
          <div className={styles.grid}>
            <TextField type="date" label={t.dateOfBirth} value={form.dateOfBirth} onChange={set('dateOfBirth')} error={error('/date_of_birth')} />
            <TextField label={t.standingHeight} value={form.standing} onChange={set('standing')} error={error('/anthropometry/height_standing_cm')} />
            <TextField label={t.sittingHeight} value={form.sitting} onChange={set('sitting')} error={error('/anthropometry/height_sitting_cm')} />
            <TextField label={t.bodyMass} value={form.mass} onChange={set('mass')} error={error('/anthropometry/body_mass_kg')} />
            <TextField type="date" label={t.launchDate} value={form.launch} onChange={set('launch')} error={error('/launch_date')} />
            <TextField type="date" label={t.landingDate} value={form.landing} onChange={set('landing')} error={error('/landing_date')} />
          </div>
          <ChoiceField label={t.sexAtBirth} options={options(t.sexOptions)} value={form.sex} onChange={set('sex')} error={error('/sex_at_birth')} />
        </fieldset>
        <SaveBar label={t.saveProfile} state={save.state} />
      </form>
    </Panel>
  );
}

interface ContextDraft {
  phase: DailyContext['phase'] | null;
  gravity: DailyContext['gravity_environment'] | null;
  day: string;
}

function ContextForm({ stored, source }: { stored: DailyContext | null; source: DataSource }): React.JSX.Element {
  const [form, setForm] = useState<ContextDraft>({
    phase: stored?.phase ?? null,
    gravity: stored?.gravity_environment ?? null,
    day: text(stored?.mission_day),
  });
  const save = useSave<DailyContext>(CONTEXT_POINTERS);
  const set =
    <K extends keyof ContextDraft>(key: K) =>
    (value: ContextDraft[K]): void => {
      save.clear();
      setForm((previous) => ({ ...previous, [key]: value }));
    };
  const error = (pointer: (typeof CONTEXT_POINTERS)[number]): string | undefined => save.state.errors.byPointer[pointer];

  const submit = (): void => {
    const day = form.day.trim() === '' ? null : parseNumber(form.day);
    const byPointer: Record<string, string> = {};
    if (form.phase === null) byPointer['/phase'] = t.needPhase;
    if (form.gravity === null) byPointer['/gravity_environment'] = t.needGravity;
    if (form.day.trim() !== '' && (day === null || !Number.isInteger(day) || day === 0)) byPointer['/mission_day'] = t.needMissionDay;
    if (form.phase === null || form.gravity === null || Object.keys(byPointer).length > 0) {
      save.reject({ byPointer, general: [] });
      return;
    }
    const fields: DailyContextFields = { phase: form.phase, gravity_environment: form.gravity, mission_day: day };
    save.run(() => source.saveTodayContext(fields));
  };

  return (
    <Panel as="section" aria-labelledby="context-heading" className={styles.panel}>
      <h2 id="context-heading">{t.contextHeading}</h2>
      <p className={styles.lede}>{t.contextLede}</p>
      <form
        className={styles.form}
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <fieldset className={styles.fields} disabled={save.state.status === 'saving'}>
          <ChoiceField label={t.phase} options={options(t.phaseOptions)} value={form.phase} onChange={set('phase')} error={error('/phase')} />
          <ChoiceField label={t.gravity} options={options(t.gravityOptions)} value={form.gravity} onChange={set('gravity')} error={error('/gravity_environment')} />
          <div className={styles.grid}>
            <TextField label={t.missionDay} value={form.day} onChange={set('day')} error={error('/mission_day')} />
          </div>
        </fieldset>
        <SaveBar label={t.saveContext} state={save.state} />
      </form>
    </Panel>
  );
}

function ContextPanel({ source }: { source: DataSource }): React.JSX.Element {
  const { data, error } = useLoad(selectContext);
  if (error !== null) return <LoadFailure error={error} fallback={t.contextError} />;
  if (data === null) return <p role="status">{t.loading}</p>;
  return <ContextForm stored={data.stored} source={source} />;
}

/** Profile: the facts the assessments need (who you are physically) and today's mission context. Nothing is invented; empty forms stay empty. */
export function ProfileView(): React.JSX.Element {
  const source = useData();
  const { data, error } = useLoad(selectProfile);
  return (
    <section aria-labelledby="profile-title" data-testid="profile-view" className={styles.profile}>
      <h1 id="profile-title">{t.title}</h1>
      {error !== null && <LoadFailure error={error} fallback={t.error} />}
      {error === null && data === null && <p role="status">{t.loading}</p>}
      {data !== null && (
        <>
          <p className={styles.lede}>{t.lede}</p>
          <p className={styles.units}>{t.units}</p>
          <div className={styles.panels}>
            <ProfileForm stored={data.stored} source={source} />
            <ContextPanel source={source} />
          </div>
        </>
      )}
    </section>
  );
}

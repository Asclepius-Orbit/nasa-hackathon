import { useCallback, useEffect, useRef, useState } from 'react';

import type { CrewMember } from '../../../shared/api';
import { useApi } from '../../api/ApiContext';
import { useAuth } from '../../auth/AuthProvider';
import { Button } from '../../components/ui/Button';
import { ChipGroup } from '../../components/ui/ChipGroup';
import pick from '../../components/ui/Pick.module.css';
import type { DataSource } from '../../data/DataSource';
import type { EscapeCode, RedFlagCode } from '../../data/types';
import { useLoad } from '../../data/useLoad';
import { useData } from '../../data/DataContext';
import { useOffline } from '../../offline/OfflineContext';
import { urgent as copy } from '../../strings/urgent';
import { useUrgent } from './UrgentContext';
import styles from './UrgentDialog.module.css';

type Code = RedFlagCode | EscapeCode;

const selectCatalog = (data: DataSource): ReturnType<DataSource['getCatalog']> => data.getCatalog();
const selectStrip = (data: DataSource): ReturnType<DataSource['getStrip']> => data.getStrip();

const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])';

function LinkIcon(): React.JSX.Element {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <path d="M4 18 20 6" />
      <path d="M8 6a8 8 0 0 1 10 10" />
      <circle cx="6" cy="18" r="2" fill="currentColor" />
    </svg>
  );
}

function ClockIcon(): React.JSX.Element {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <circle cx="12" cy="12" r="8" />
      <path d="M12 8v4l3 2" />
    </svg>
  );
}

/** The crew members to choose from, read only when nobody is unlocked. `null` while loading; empty when none could be read. */
function useCrewChoices(enabled: boolean): CrewMember[] | null {
  const api = useApi();
  const [crew, setCrew] = useState<CrewMember[] | null>(null);
  useEffect(() => {
    if (!enabled) return undefined;
    let live = true;
    api.crew.list().then(
      (list) => {
        if (live) setCrew(list);
      },
      () => {
        if (live) setCrew([]);
      },
    );
    return () => {
      live = false;
    };
  }, [api, enabled]);
  return crew;
}

/** Crew names, only shown when nobody is unlocked (login or lock screen): the report is sent for the one chosen, without a PIN. */
function CrewPicker({ crew, chosen, onChoose }: { crew: readonly CrewMember[]; chosen: string | null; onChoose: (id: string) => void }): React.JSX.Element {
  return (
    <div role="group" aria-label={copy.forGroup} className={styles.crew}>
      <span className={styles.crewLabel}>{copy.forLabel}</span>
      {crew.map((member) => (
        <button
          key={member.id}
          type="button"
          aria-pressed={chosen === member.id}
          className={pick.pick}
          onClick={() => {
            onChoose(member.id);
          }}
        >
          {member.name}
        </button>
      ))}
    </div>
  );
}

function Panel(): React.JSX.Element {
  const { close, preselected } = useUrgent();
  const { status, crewId } = useAuth();
  const { offline } = useOffline();
  const catalog = useLoad(selectCatalog).data;
  const strip = useLoad(selectStrip).data;
  const [selected, setSelected] = useState<Code[]>([...preselected]);
  const data = useData();
  const [sent, setSent] = useState(false);
  const [chosen, setChosen] = useState<string | null>(null);
  const nobodyUnlocked = status === 'signed-out' || status === 'locked';
  const crew = useCrewChoices(nobodyUnlocked);
  // Signed in: the report is for that member. Otherwise it is for the one picked here.
  const target = status === 'signed-in' ? crewId : chosen;
  const [notRecorded, setNotRecorded] = useState(false);
  const dialogRef = useRef<HTMLDivElement>(null);
  const [opener] = useState(() => document.activeElement);

  useEffect(() => {
    dialogRef.current?.focus();
    return () => {
      if (opener instanceof HTMLElement) opener.focus();
    };
  }, [opener]);

  // Send disables itself once sent; a focused disabled button drops keyboard events, so keep focus on the dialog (Escape must still close it).
  useEffect(() => {
    if (sent) dialogRef.current?.focus();
  }, [sent]);

  const onKeyDown = useCallback(
    (event: React.KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        close();
        return;
      }
      if (event.key !== 'Tab' || dialogRef.current === null) return;
      const items = [...dialogRef.current.querySelectorAll<HTMLElement>(FOCUSABLE)];
      const first = items[0];
      const last = items[items.length - 1];
      if (first === undefined || last === undefined) return;
      const active = document.activeElement;
      if (event.shiftKey && (active === first || active === dialogRef.current)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    },
    [close],
  );

  const options = catalog ? [...catalog.flags, ...catalog.escapes].map((o) => ({ value: o.code, label: o.label })) : [];
  // An unknown ground link counts as no link: the report is queued, never promised as sent.
  const noLink = offline || strip === null || strip.groundLink === null || strip.groundLink.state === 'down';
  const backInMin = strip?.groundLink?.state === 'down' ? strip.groundLink.backInMin : null;

  return (
    <div className={styles.backdrop}>
      {/* eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions -- the modal dialog owns Escape and the Tab trap */}
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="urgent-title"
        tabIndex={-1}
        className={styles.dialog}
        data-testid="urgent-dialog"
        onKeyDown={onKeyDown}
      >
        <h2 id="urgent-title">{copy.title}</h2>
        {nobodyUnlocked && crew !== null && crew.length > 0 && <CrewPicker crew={crew} chosen={chosen} onChoose={setChosen} />}
        {nobodyUnlocked && crew !== null && crew.length === 0 && <p className={styles.notRecorded}>{copy.noCrew}</p>}
        <p className={styles.intro}>{data.sendsUrgentReports ? copy.intro : copy.introNotSent}</p>
        <div className={styles.flags}>
          <ChipGroup label={copy.symptomsGroup} options={options} selected={selected} tone="flag" onChange={setSelected} />
        </div>
        <p className={styles.pending}>
          <span className={styles.tag}>{copy.pendingTag}</span> {copy.pendingNote}
        </p>
        {data.sendsUrgentReports && (
        <div className={styles.delivery}>
          {sent ? <ClockIcon /> : <LinkIcon />}
          <span>
            {sent ? (
              <>
                <b>{copy.queuedTitle}</b>
                {copy.queuedBody}
              </>
            ) : noLink ? (
              <>
                <b>{copy.noLinkTitle}</b>
                {copy.noLinkBody(backInMin)}
              </>
            ) : (
              <>
                <b>{copy.linkUpTitle}</b>
                {copy.linkUpBody}
              </>
            )}
          </span>
        </div>
        )}
        {nobodyUnlocked && crew !== null && crew.length > 0 && chosen === null && selected.length > 0 && <p className={styles.pending}>{copy.chooseWho}</p>}
        {notRecorded && (
          <p role="alert" className={styles.notRecorded}>
            <b>{copy.notRecordedTitle}</b> {copy.notRecordedBody}
          </p>
        )}
        <div className={styles.actions}>
          <Button
            variant="urgent"
            disabled={selected.length === 0 || sent || target === null}
            onClick={() => {
              if (target === null) return;
              setNotRecorded(false);
              data.sendUrgentReport(target, selected).then(
                () => {
                  setSent(true);
                },
                () => {
                  setNotRecorded(true);
                },
              );
            }}
          >
            {sent ? copy.queued : copy.send}
          </Button>
          <Button variant="quiet" onClick={close}>
            {copy.close}
          </Button>
        </div>
      </div>
    </div>
  );
}

/** The Urgent dialog. Needs no session: it works from the login and lock screens too. */
export function UrgentDialog(): React.JSX.Element | null {
  const { isOpen } = useUrgent();
  return isOpen ? <Panel /> : null;
}

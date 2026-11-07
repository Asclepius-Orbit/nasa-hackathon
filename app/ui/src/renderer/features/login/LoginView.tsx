import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router';

import type { CrewMember } from '../../../shared/api';
import { useApi } from '../../api/ApiContext';
import { useAuth } from '../../auth/AuthProvider';
import { useNow } from '../../clock/Clock';
import { MockSkipLogin } from '../../components/layout/MockSkipLogin';
import { Button } from '../../components/ui/Button';
import { cx } from '../../components/ui/cx';
import { Keypad } from '../../components/ui/Keypad';
import { NoData } from '../../components/ui/Panel';
import type { DataSource } from '../../data/DataSource';
import { useLoad } from '../../data/useLoad';
import { useSettings } from '../../settings/SettingsProvider';
import { groundLinkValue } from '../../strings/common';
import { cleanName, createFailureMessage, initials, login, pinFailureMessage } from '../../strings/login';
import { useUrgent } from '../urgent/UrgentContext';
import styles from './Login.module.css';
import { NewMemberForm } from './NewMemberForm';
import { PinDots } from './PinDots';
import { usePinEntry } from './usePinEntry';

const selectStrip = (data: DataSource): ReturnType<DataSource['getStrip']> => data.getStrip();

type Step = 'pick' | 'pin' | 'name' | 'newpin';

/** "Down, back in 12 min": the state word in bold, the rest plain (as in the mock). */
function GroundLinkFact({ value }: { value: string }): React.JSX.Element {
  const comma = value.indexOf(',');
  return comma === -1 ? <b>{value}</b> : <><b>{value.slice(0, comma)}</b>{value.slice(comma)}</>;
}

function SidePanel(): React.JSX.Element {
  const { data } = useLoad(selectStrip);
  const now = useNow();
  const { formatTime } = useSettings();
  return (
    <div className={styles.side}>
      <span className={styles.mark}>{login.mark}</span>
      <h1 id="login-title">{login.title}</h1>
      <p>{login.intro}</p>
      <p className={styles.privacy}>{login.privacy}</p>
      <dl className={styles.facts} aria-label={login.factsLabel}>
        <dt>{login.gmt}</dt>
        <dd>
          {data === null ? (
            <NoData />
          ) : (
            <>
              <b>{formatTime(now, new Date(data.missionStart))}</b>, {login.missionDay(data.missionDay)}
            </>
          )}
        </dd>
        <dt>{login.groundLink}</dt>
        <dd>{data === null ? <NoData /> : <GroundLinkFact value={groundLinkValue(data.groundLink)} />}</dd>
        <dt>{login.dataStays}</dt>
        <dd>{login.dataStaysValue}</dd>
      </dl>
    </div>
  );
}

/** Login: pick a crew tile and enter its PIN, or add a new crew member (name, then a PIN chosen twice). */
export function LoginView(): React.JSX.Element {
  const api = useApi();
  const { signIn } = useAuth();
  const navigate = useNavigate();
  const { open } = useUrgent();

  const [members, setMembers] = useState<CrewMember[] | null>(null);
  const [listFailed, setListFailed] = useState(false);
  const [step, setStep] = useState<Step>('pick');
  const [me, setMe] = useState<CrewMember | null>(null);
  const [newName, setNewName] = useState('');
  const [code, setCode] = useState<string | null>(null);
  const [nameError, setNameError] = useState('');
  const [pinError, setPinError] = useState('');
  const [repeating, setRepeating] = useState(false);
  const [busy, setBusy] = useState(false);
  const firstPin = useRef<string | null>(null);

  useEffect(() => {
    let live = true;
    api.crew.list().then(
      (list) => {
        if (live) setMembers(list);
      },
      () => {
        if (live) setListFailed(true);
      },
    );
    return () => {
      live = false;
    };
  }, [api]);

  const enter = useCallback(
    (crewId: string) => {
      signIn({ ok: true, crewId });
      void navigate('/today');
    },
    [signIn, navigate],
  );

  const restartNewPin = (message: string): void => {
    firstPin.current = null;
    setRepeating(false);
    setPinError(message);
  };

  const submitExisting = (pin: string): void => {
    if (me === null) return;
    setBusy(true);
    api.auth.verifyPin({ crewId: me.id, pin }).then(
      (result) => {
        setBusy(false);
        if (result.ok) enter(result.crewId);
        else setPinError(pinFailureMessage(result));
      },
      () => {
        setBusy(false);
        setPinError(login.unavailable);
      },
    );
  };

  const submitNew = (pin: string): void => {
    if (firstPin.current === null) {
      firstPin.current = pin;
      setRepeating(true);
      setPinError('');
      return;
    }
    if (pin !== firstPin.current) {
      restartNewPin(login.pinMismatch);
      return;
    }
    firstPin.current = null;
    setBusy(true);
    api.crew.create({ name: newName, pin }).then(
      (result) => {
        setBusy(false);
        if (result.ok) {
          enter(result.member.id);
          return;
        }
        const message = createFailureMessage(result, newName);
        if (result.reason === 'name_too_short' || result.reason === 'name_taken') {
          setNameError(message);
          setStep('name');
        } else restartNewPin(message);
      },
      () => {
        setBusy(false);
        restartNewPin(login.unavailable);
      },
    );
  };

  const pinStep = step === 'pin' || step === 'newpin';
  const pad = usePinEntry(step === 'pin' ? submitExisting : submitNew, pinStep && !busy);

  const pickMember = (member: CrewMember): void => {
    pad.clear();
    setMe(member);
    setPinError('');
    setStep('pin');
  };

  const startNew = (): void => {
    pad.clear();
    setMe(null);
    setNameError('');
    setCode(null);
    setStep('name');
    api.crew.nextId().then(setCode, () => {
      setCode(null);
    });
  };

  const submitName = (raw: string): void => {
    const name = cleanName(raw);
    if (name.length < 2) {
      setNameError(login.nameTooShort);
      return;
    }
    if ((members ?? []).some((m) => m.name.toLowerCase() === name.toLowerCase())) {
      setNameError(login.nameTaken(name));
      return;
    }
    setNameError('');
    setNewName(name);
    restartNewPin('');
    pad.clear();
    setStep('newpin');
  };

  const backToPick = (): void => {
    pad.clear();
    setStep('pick');
  };

  const showTiles = step === 'pick' || step === 'pin';
  let pinTitle = '';
  let pinHint = '';
  if (step === 'pin' && me !== null) pinTitle = login.pinFor(me.name);
  if (step === 'newpin') {
    pinTitle = repeating ? login.repeatPin : login.choosePin(newName);
    pinHint = repeating ? login.repeatPinHint : login.choosePinHint;
  }

  return (
    <section aria-labelledby="login-title" data-testid="login-view" className={styles.login}>
      <SidePanel />
      <div className={styles.main}>
        {showTiles && (
          <>
            <h2>{login.who}</h2>
            <div className={styles.tiles}>
              {members?.map((member) => (
                <button
                  key={member.id}
                  type="button"
                  className={styles.tile}
                  aria-pressed={me?.id === member.id && step === 'pin'}
                  onClick={() => {
                    pickMember(member);
                  }}
                >
                  <span className={styles.ini} aria-hidden="true">
                    {initials(member.name)}
                  </span>
                  <b className={styles.name}>{member.name}</b>
                  <span>{member.harnessId === null ? login.notPaired : login.harness(member.harnessId)}</span>
                </button>
              ))}
              {members !== null && (
                <button type="button" className={cx(styles.tile, styles.other)} aria-pressed={false} onClick={startNew}>
                  <b>{login.newTileTitle}</b>
                  <span>{login.newTileHint}</span>
                </button>
              )}
            </div>
            {listFailed && (
              <p className={styles.error} role="alert">
                {login.crewLoadError}
              </p>
            )}
          </>
        )}
        {step === 'name' && <NewMemberForm code={code} initialName={newName} error={nameError} onSubmit={submitName} onCancel={backToPick} />}
        {pinStep && (
          <div className={styles.pinStep}>
            <h2>{pinTitle}</h2>
            <PinDots length={pad.length} />
            {pinHint !== '' && <p className={styles.hint}>{pinHint}</p>}
            <p className={styles.error} role="alert">
              {pinError}
            </p>
            <Keypad onDigit={pad.digit} onDelete={pad.remove} onEnter={pad.enter} disabled={busy} />
          </div>
        )}
        {step === 'newpin' && (
          <Button
            variant="quiet"
            className={cx(styles.quietOnDark, styles.alignStart)}
            onClick={() => {
              pad.clear();
              setStep('name');
            }}
          >
            {login.backToName}
          </Button>
        )}
        <Button
          variant="urgent"
          className={styles.urgent}
          onClick={() => {
            open();
          }}
        >
          {login.urgent}
        </Button>
        <MockSkipLogin />
      </div>
    </section>
  );
}

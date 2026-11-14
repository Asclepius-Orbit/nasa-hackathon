import { useEffect, useRef, useState } from 'react';

import { Button } from '../../components/ui/Button';
import { login } from '../../strings/login';
import styles from './Login.module.css';

export interface NewMemberFormProps {
  /** Crew code the new member would get, or `null` while it loads. */
  code: string | null;
  initialName: string;
  error: string;
  onSubmit: (rawName: string) => void;
  onCancel: () => void;
}

/** Name step of "New crew member". The name is validated by the caller, then the PIN is chosen twice. */
export function NewMemberForm({ code, initialName, error, onSubmit, onCancel }: NewMemberFormProps): React.JSX.Element {
  const [name, setName] = useState(initialName);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    input.current?.focus();
  }, []);
  return (
    <form
      className={styles.newMember}
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit(name);
      }}
    >
      <h2>{login.newTitle}</h2>
      <label htmlFor="nm-name">{login.nameLabel}</label>
      <input
        id="nm-name"
        ref={input}
        type="text"
        autoComplete="off"
        maxLength={40}
        placeholder={login.namePlaceholder}
        value={name}
        onChange={(event) => {
          setName(event.target.value);
        }}
      />
      <p className={styles.hint}>
        {login.codeHint} <b>{code ?? '...'}</b>.
      </p>
      <p className={styles.error} role="alert">
        {error}
      </p>
      <div className={styles.buttons}>
        <Button type="submit">{login.next}</Button>
        <Button variant="quiet" className={styles.quietOnDark} onClick={onCancel}>
          {login.cancel}
        </Button>
      </div>
    </form>
  );
}

import styles from './Button.module.css';
import { cx } from './cx';

export type ButtonVariant = 'primary' | 'quiet' | 'urgent';

export type ButtonProps = React.ComponentPropsWithoutRef<'button'> & {
  /** `primary` is the main action, `quiet` an outline for secondary ones, `urgent` the red alert action. */
  variant?: ButtonVariant;
};

/** 64 px touch-target button; `type` defaults to `button` so it never submits a form by accident. */
export function Button({ variant = 'primary', type = 'button', className, ...rest }: ButtonProps): React.JSX.Element {
  return (
    <button
      {...rest}
      type={type}
      data-variant={variant}
      className={cx(styles.button, className)}
    />
  );
}

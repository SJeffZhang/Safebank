import { useId } from 'react';
import type { InputHTMLAttributes, ReactNode } from 'react';
import { Icon } from './Icon';
import styles from './TextField.module.css';

export interface TextFieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'size'> {
  label: string;
  hint?: string;
  error?: string;
  leading?: ReactNode;
  containerClassName?: string;
}

export function TextField({
  label,
  hint,
  error,
  leading,
  id,
  required,
  disabled,
  className,
  containerClassName,
  ...rest
}: TextFieldProps) {
  const autoId = useId();
  const inputId = id ?? `sb-field-${autoId}`;
  const hintId = hint ? `${inputId}-hint` : undefined;
  const errorId = error ? `${inputId}-error` : undefined;
  const describedBy = [hintId, errorId].filter(Boolean).join(' ') || undefined;

  const classes = [styles.field, error ? styles.hasError : '', disabled ? styles.disabled : '', containerClassName ?? '']
    .filter(Boolean)
    .join(' ');

  return (
    <div className={classes}>
      <label className={styles.label} htmlFor={inputId}>
        {label}
        {required ? (
          <span className={styles.required} aria-hidden="true">
            必填
          </span>
        ) : null}
      </label>

      <div className={styles.control}>
        {leading ? <span className={styles.leading}>{leading}</span> : null}
        <input
          {...rest}
          id={inputId}
          className={[styles.input, className ?? ''].filter(Boolean).join(' ')}
          required={required}
          disabled={disabled}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy}
        />
      </div>

      {hint ? (
        <p className={styles.hint} id={hintId}>
          {hint}
        </p>
      ) : null}

      {error ? (
        <p className={styles.error} id={errorId} role="alert">
          <Icon name="alert" size={13} />
          <span>{error}</span>
        </p>
      ) : null}
    </div>
  );
}

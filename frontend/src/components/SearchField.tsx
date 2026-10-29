import { useId } from 'react';
import { Icon } from './Icon';
import { IconButton } from './Button';
import styles from './TextField.module.css';

export interface SearchFieldProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  onClear?: () => void;
  placeholder?: string;
  hint?: string;
  disabled?: boolean;
  id?: string;
}

export function SearchField({
  label,
  value,
  onChange,
  onClear,
  placeholder = '搜索',
  hint,
  disabled = false,
  id,
}: SearchFieldProps) {
  const autoId = useId();
  const inputId = id ?? `sb-search-${autoId}`;
  const hintId = hint ? `${inputId}-hint` : undefined;

  return (
    <div className={[styles.field, disabled ? styles.disabled : ''].filter(Boolean).join(' ')}>
      <label className="sb-sr-only" htmlFor={inputId}>
        {label}
      </label>
      <div className={[styles.control, styles.searchControl].join(' ')}>
        <span className={styles.leading} aria-hidden="true">
          <Icon name="search" size={18} />
        </span>
        <input
          id={inputId}
          type="search"
          className={styles.input}
          value={value}
          placeholder={placeholder}
          disabled={disabled}
          aria-describedby={hintId}
          onChange={(event) => onChange(event.target.value)}
        />
        {value ? (
          <IconButton
            label="清空搜索"
            className={styles.clear}
            onClick={() => {
              onChange('');
              onClear?.();
            }}
          >
            <Icon name="close" size={17} />
          </IconButton>
        ) : null}
      </div>
      {hint ? (
        <p className={styles.hint} id={hintId}>
          {hint}
        </p>
      ) : null}
    </div>
  );
}

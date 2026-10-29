import type { ReactNode } from 'react';
import { IconButton } from './Button';
import { Icon } from './Icon';
import styles from './TopBar.module.css';

export interface TopBarProps {
  title: string;
  subtitle?: string;
  onBack?: () => void;
  backLabel?: string;
  actions?: ReactNode;
}

export function TopBar({ title, subtitle, onBack, backLabel = '返回', actions }: TopBarProps) {
  return (
    <header className={styles.topbar}>
      {onBack ? (
        <IconButton label={backLabel} onClick={onBack}>
          <Icon name="chevron-left" size={20} />
        </IconButton>
      ) : (
        <span className={styles.placeholder} aria-hidden="true" />
      )}
      <div className={styles.heading}>
        <h1 className={styles.title}>{title}</h1>
        {subtitle ? <p className={styles.subtitle}>{subtitle}</p> : null}
      </div>
      <div className={styles.side}>{actions}</div>
    </header>
  );
}

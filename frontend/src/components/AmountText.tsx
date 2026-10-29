import { IconButton } from './Button';
import { Icon } from './Icon';
import styles from './AmountText.module.css';

export interface AmountTextProps {
  /** Amount in cents; always integer to avoid float drift. */
  amountCents: number;
  hidden?: boolean;
  onToggleHidden?: () => void;
  signed?: boolean;
  size?: 'md' | 'lg' | 'xl';
  label?: string;
  className?: string;
}

const numberFormatter = new Intl.NumberFormat('zh-CN', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export function formatCents(amountCents: number, signed = false): string {
  const value = Math.abs(Math.round(amountCents));
  const sign = amountCents < 0 ? '-' : signed && amountCents > 0 ? '+' : '';
  return `${sign}¥${numberFormatter.format(value / 100)}`;
}

export function AmountText({
  amountCents,
  hidden = false,
  onToggleHidden,
  signed = false,
  size = 'md',
  label = '金额',
  className,
}: AmountTextProps) {
  const trend = amountCents > 0 ? 'up' : amountCents < 0 ? 'down' : 'none';
  const text = formatCents(amountCents, signed);

  return (
    <span className={[styles.root, className ?? ''].filter(Boolean).join(' ')}>
      {hidden ? (
        <>
          <span className={`${styles.value} ${styles[size]}`} aria-hidden="true">
            <span className={styles.mask}>••••••</span>
          </span>
          <span className="sb-sr-only">{label}已隐藏</span>
        </>
      ) : (
        <span
          className={[styles.value, styles[size], trend === 'up' ? styles.up : '', trend === 'down' ? styles.down : '']
            .filter(Boolean)
            .join(' ')}
          data-trend={trend}
        >
          {text}
        </span>
      )}

      {onToggleHidden ? (
        <IconButton
          label={hidden ? `显示${label}` : `隐藏${label}`}
          aria-pressed={hidden}
          className={styles.toggle}
          onClick={onToggleHidden}
        >
          <Icon name={hidden ? 'eye-off' : 'eye'} size={16} />
        </IconButton>
      ) : null}
    </span>
  );
}

import styles from './Skeleton.module.css';

export interface SkeletonProps {
  width?: number | string;
  height?: number | string;
  radius?: number | string;
}

export function Skeleton({ width = '100%', height = 12, radius }: SkeletonProps) {
  return (
    <span
      className={styles.skeleton}
      style={{ width, height, borderRadius: radius }}
      aria-hidden="true"
    />
  );
}

export function SkeletonText({ lines = 3 }: { lines?: number }) {
  return (
    <span className={styles.text} aria-hidden="true">
      {Array.from({ length: lines }).map((_, index) => (
        <Skeleton key={index} width={index === lines - 1 ? '62%' : '100%'} height={11} />
      ))}
    </span>
  );
}

export function LoadingIndicator({ label = '正在加载' }: { label?: string }) {
  return (
    <span role="status" aria-live="polite" className={styles.text}>
      <span className="sb-sr-only">{label}</span>
      <Skeleton height={14} />
      <SkeletonText lines={2} />
    </span>
  );
}

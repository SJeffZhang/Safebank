import styles from './StatusBar.module.css';

/**
 * 手机系统状态区：与目标简报的 .sbar / .island 一致。
 * 9:41 + 信号 / Wi-Fi / 电池，外加 Dynamic Island。
 */
export function StatusBar() {
  return (
    <div className={styles.sbar} aria-hidden="true">
      <span className={styles.time}>9:41</span>
      <span className={styles.icons}>
        <i className={styles.sig} />
        <i className={styles.wifi} />
        <i className={styles.batt} />
      </span>
    </div>
  );
}

export function DynamicIsland() {
  return <span className={styles.island} aria-hidden="true" />;
}
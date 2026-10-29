import { Icon } from './Icon';
import type { IconName } from './Icon';
import styles from './BottomNav.module.css';

export type NavKey = 'home' | 'assistant' | 'profile';

const sideItems: { key: NavKey; label: string; icon: IconName }[] = [
  { key: 'home', label: '首页', icon: 'home' },
  { key: 'profile', label: '个人', icon: 'person' },
];

export interface BottomNavProps {
  value: NavKey;
  onChange: (value: NavKey) => void;
}

/**
 * C2 三栏下凹导航：首页 / 中央石墨小安圆钮 / 个人。
 * 形状与尺寸对齐目标 .c2nav（94u 高、76u 形状、58u items、68u 中央占位、50u 圆钮）。
 * DOM 顺序保持「首页 → 小安 → 个人」，读屏与键盘顺序与视觉一致。
 */
export function BottomNav({ value, onChange }: BottomNavProps) {
  const assistantActive = value === 'assistant';

  const renderTab = (item: (typeof sideItems)[number], withSlot: boolean) => {
    const active = item.key === value && !assistantActive;
    return (
      <span className={styles.group}>
        {withSlot ? <span className={styles.slot} aria-hidden="true" /> : null}
        <button
          type="button"
          className={[styles.tab, active ? styles.tabActive : ''].filter(Boolean).join(' ')}
          aria-current={active ? 'page' : undefined}
          onClick={() => onChange(item.key)}
        >
          <Icon name={item.icon} size={18} />
          <span>{item.label}</span>
        </button>
      </span>
    );
  };

  return (
    <nav className={styles.nav} aria-label="主导航">
      <svg
        className={styles.shape}
        viewBox="0 0 375 76"
        preserveAspectRatio="none"
        aria-hidden="true"
        focusable="false"
      >
        <path d="M0 12 H126 C154 12 154 42 187.5 42 C221 42 221 12 249 12 H375 V76 H0 Z" />
      </svg>

      <div className={styles.items}>
        {renderTab(sideItems[0], false)}

        <button
          type="button"
          className={[styles.agent, assistantActive ? styles.agentActive : ''].filter(Boolean).join(' ')}
          aria-label="小安"
          aria-current={assistantActive ? 'page' : undefined}
          onClick={() => onChange('assistant')}
        >
          <span className={styles.disc}>
            <Icon name="agent-mark" size={22} />
          </span>
          {/* 视觉上不显示文字，但保证读屏能拿到「小安」这个入口名 */}
          <span className="sb-sr-only">小安</span>
        </button>

        {renderTab(sideItems[1], true)}
      </div>

      <span className={styles.hind} aria-hidden="true" />
    </nav>
  );
}
import type { ReactNode } from 'react';
import { Button } from './Button';
import { Icon } from './Icon';
import type { IconName } from './Icon';
import { Skeleton, SkeletonText } from './Skeleton';
import styles from './StateView.module.css';

export type ViewState =
  | 'initial'
  | 'loading'
  | 'loaded'
  | 'empty'
  | 'error'
  | 'offline'
  | 'disabled'
  | 'submitting'
  | 'success'
  | 'failed'
  | 'unknown';

interface Defaults {
  title: string;
  description: string;
  icon: IconName;
  tone: 'neutral' | 'success' | 'danger';
}

const defaults: Record<Exclude<ViewState, 'loaded'>, Defaults> = {
  initial: {
    title: '准备中',
    description: '正在建立页面骨架，随后会自动加载。',
    icon: 'clock',
    tone: 'neutral',
  },
  loading: {
    title: '正在加载',
    description: '内容加载完成后会自动出现。',
    icon: 'clock',
    tone: 'neutral',
  },
  empty: {
    title: '暂无内容',
    description: '这里还没有数据，可以先完成一次操作，或返回上一页。',
    icon: 'info',
    tone: 'neutral',
  },
  error: {
    title: '加载失败',
    description: '请检查网络后重试；如果持续失败，请稍后再来。',
    icon: 'alert',
    tone: 'danger',
  },
  offline: {
    title: '当前处于离线状态',
    description: '已展示的内容会保留，恢复网络后可以重新加载。',
    icon: 'wifi-off',
    tone: 'neutral',
  },
  disabled: {
    title: '暂不可用',
    description: '当前条件不满足，完成必要步骤后会自动恢复。',
    icon: 'lock',
    tone: 'neutral',
  },
  submitting: {
    title: '正在提交',
    description: '请稍候，不要重复操作。',
    icon: 'clock',
    tone: 'neutral',
  },
  success: {
    title: '操作已完成',
    description: '结果已确认，可以继续下一步。',
    icon: 'check',
    tone: 'success',
  },
  failed: {
    title: '操作未完成',
    description: '操作未完成，请查看详情或根据提示决定下一步。',
    icon: 'alert',
    tone: 'danger',
  },
  unknown: {
    title: '结果待确认',
    description: '请求已提交，但结果尚未返回。请勿重复提交，可查看状态。',
    icon: 'clock',
    tone: 'neutral',
  },
};

export interface StateViewProps {
  state: ViewState;
  title?: string;
  description?: string;
  actionLabel?: string;
  onAction?: () => void;
  secondaryActionLabel?: string;
  onSecondaryAction?: () => void;
  compact?: boolean;
  children?: ReactNode;
}

export function StateView({
  state,
  title,
  description,
  actionLabel,
  onAction,
  secondaryActionLabel,
  onSecondaryAction,
  compact = false,
  children,
}: StateViewProps) {
  if (state === 'loaded') return <>{children}</>;

  const preset = defaults[state];
  const isLoading = state === 'loading' || state === 'initial' || state === 'submitting';
  const iconClass = [
    styles.icon,
    preset.tone === 'success' ? styles.successIcon : '',
    preset.tone === 'danger' ? styles.dangerIcon : '',
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <div
      className={[styles.root, compact ? styles.compact : ''].filter(Boolean).join(' ')}
      role={state === 'error' || state === 'failed' ? 'alert' : 'status'}
      aria-live={state === 'error' || state === 'failed' ? 'assertive' : 'polite'}
    >
      {isLoading ? (
        <div className={styles.skeleton} aria-hidden="true">
          <Skeleton height={14} />
          <SkeletonText lines={3} />
        </div>
      ) : (
        <span className={iconClass} aria-hidden="true">
          <Icon name={preset.icon} size={20} />
        </span>
      )}

      <p className={styles.title}>{title ?? preset.title}</p>
      <p className={styles.description}>{description ?? preset.description}</p>

      {isLoading ? (
        <span className={styles.loadingText}>{state === 'submitting' ? '正在提交…' : '正在加载…'}</span>
      ) : null}

      {actionLabel || secondaryActionLabel ? (
        <div className={styles.actions}>
          {actionLabel ? (
            <Button variant="secondary" onClick={onAction}>
              {actionLabel}
            </Button>
          ) : null}
          {secondaryActionLabel ? (
            <Button variant="ghost" onClick={onSecondaryAction}>
              {secondaryActionLabel}
            </Button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

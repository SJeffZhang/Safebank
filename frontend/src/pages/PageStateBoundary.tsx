import type { ReactNode } from 'react';
import { StateView } from '../components/StateView';
import type { ViewState } from '../components/StateView';

interface Props {
  state: ViewState;
  onRetry?: () => void;
  onViewStatus?: () => void;
  emptyActionLabel?: string;
  onEmptyAction?: () => void;
  children: ReactNode;
}

/**
 * 统一的状态接入位置：state === 'loaded' 渲染内容，否则渲染对应 StateView。
 * 页面只负责选择状态并提供出口，不复制 loading / empty / error 等分支。
 */
export function PageStateBoundary({
  state,
  onRetry,
  onViewStatus,
  emptyActionLabel,
  onEmptyAction,
  children,
}: Props) {
  if (state === 'loaded') return <>{children}</>;

  const isRecoverable = state === 'error' || state === 'offline' || state === 'failed';
  const actionLabel =
    state === 'empty' ? (emptyActionLabel ?? '去操作') : isRecoverable ? '重试' : state === 'unknown' ? '查看状态' : undefined;
  const onAction = state === 'empty' ? onEmptyAction : isRecoverable ? onRetry : state === 'unknown' ? onViewStatus : undefined;

  return <StateView state={state} actionLabel={actionLabel} onAction={onAction} />;
}
import { useCallback, useRef, useState } from 'react';
import { ApprovalSheet } from '../../components/ApprovalSheet';
import type { ApprovalSummary } from '../../components/ApprovalSheet';
import { GatewayOfflineError } from '../../services/approval/shared';
import { useApprovalFlow } from '../../services/approval/useApprovalFlow';
import type { WealthService } from '../../services/wealth/WealthService';
import type { WealthPlanView } from '../../services/wealth/types';
import { makePendingAccessors, WEALTH_PENDING_APPROVAL_KEY } from '../../app/viewState';
import { formatAmount, formatNetValue, formatShares } from './format';

const wealthPending = makePendingAccessors(WEALTH_PENDING_APPROVAL_KEY);

type RetryContext =
  | { kind: 'purchase'; productId: string; amountCents: number }
  | { kind: 'redemption'; holdingId: string; shares: number };

/**
 * 理财申购/赎回共用的审批接线：复用公共 useApprovalFlow，
 * 负责预演、过期重试上下文、成功后重新获取权威数据与「刷新失败」独立提示。
 */
export function useWealthApproval(service: WealthService, onRefetch: () => Promise<boolean>) {
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const retryRef = useRef<RetryContext | null>(null);

  const approval = useApprovalFlow<WealthPlanView>(service, wealthPending, {
    onSuccess: () => {
      void onRefetch().then((ok) => {
        if (!ok) setRefreshError('操作已完成，但最新资产暂未刷新，可重试');
        else setRefreshError(null);
      });
    },
  });

  const startPurchase = useCallback(
    async (productId: string, amountCents: number) => {
      retryRef.current = { kind: 'purchase', productId, amountCents };
      const seq = approval.actions.startPreview();
      try {
        const res = await service.preparePurchase(productId, amountCents);
        if (!approval.actions.isCurrent(seq)) return;
        if (res.kind === 'error') {
          approval.actions.previewFailed(res.message, false);
          return;
        }
        approval.actions.begin(res.approvalId);
      } catch (error) {
        if (!approval.actions.isCurrent(seq)) return;
        const offline = error instanceof GatewayOfflineError;
        approval.actions.previewFailed(offline ? '网络不可用，请恢复后重试' : '预演失败，请稍后重试', offline);
      }
    },
    [service, approval],
  );

  const startRedemption = useCallback(
    async (holdingId: string, shares: number) => {
      retryRef.current = { kind: 'redemption', holdingId, shares };
      const seq = approval.actions.startPreview();
      try {
        const res = await service.prepareRedemption(holdingId, shares);
        if (!approval.actions.isCurrent(seq)) return;
        if (res.kind === 'error') {
          approval.actions.previewFailed(res.message, false);
          return;
        }
        approval.actions.begin(res.approvalId);
      } catch (error) {
        if (!approval.actions.isCurrent(seq)) return;
        const offline = error instanceof GatewayOfflineError;
        approval.actions.previewFailed(offline ? '网络不可用，请恢复后重试' : '预演失败，请稍后重试', offline);
      }
    },
    [service, approval],
  );

  /** 过期后「重新预演」：使用当前产品与当前金额/份额重新 prepare（生成新 approval_id）。 */
  const retryPreview = useCallback(() => {
    const ctx = retryRef.current;
    if (!ctx) return;
    if (ctx.kind === 'purchase') void startPurchase(ctx.productId, ctx.amountCents);
    else void startRedemption(ctx.holdingId, ctx.shares);
  }, [startPurchase, startRedemption]);

  const clearRetryContext = useCallback(() => {
    retryRef.current = null;
  }, []);

  const dismissRefreshError = useCallback(() => setRefreshError(null), []);

  return {
    approval,
    refreshError,
    dismissRefreshError,
    startPurchase,
    startRedemption,
    retryPreview,
    clearRetryContext,
  };
}

export type WealthApproval = ReturnType<typeof useWealthApproval>;

export interface WealthApprovalSheetProps {
  approval: WealthApproval;
  hidden: boolean;
}

/** 把 WealthPlanView 映射到 ApprovalSheet，并渲染。 */
export function WealthApprovalSheet({ approval, hidden }: WealthApprovalSheetProps) {
  const { approval: flow, clearRetryContext } = approval;
  const state = flow.state;
  const plan = state.planView;

  const rows: ApprovalSummary['rows'] = plan
    ? [
        { label: '产品', value: plan.productName, emphasis: true },
        { label: '风险等级', value: plan.riskBand },
        ...(plan.operation === 'purchase'
          ? [
              { label: '申购金额', value: formatAmount(plan.amountCents ?? 0, hidden), emphasis: true },
              { label: '资金来源', value: plan.sourceText },
            ]
          : [
              { label: '赎回份额', value: `${formatShares(plan.shares ?? 0)} 份`, emphasis: true },
              {
                label: '预计金额',
                value: formatAmount(plan.estimatedAmountCents ?? 0, hidden),
              },
              { label: '单位净值', value: formatNetValue(plan.netValueForDisplay ?? 0) },
            ]),
        { label: '费用', value: plan.feeText },
        { label: '到账说明', value: plan.arrivalText },
      ]
    : [];

  const summary: ApprovalSummary = plan
    ? {
        title: plan.operation === 'purchase' ? '申购确认' : '赎回确认',
        subtitle: `${plan.productName} · ${plan.riskBand}`,
        rows,
        riskNotes: plan.riskNotes,
      }
    : { title: '' };

  const sheetState =
    state.flow === 'CONFIRMING'
      ? 'confirming'
      : state.flow === 'SUCCESS'
        ? 'success'
        : state.flow === 'FAILED'
          ? 'failed'
          : state.flow === 'UNKNOWN'
            ? 'unknown'
            : 'ready';

  return (
    <ApprovalSheet
      open={state.sheetOpen}
      approvalId={state.approvalId ?? ''}
      mode={plan?.riskLevel ?? 'yellow'}
      state={sheetState}
      summary={summary}
      riskAckRequired={Boolean(plan?.ackRequired)}
      riskDisclosure={
        plan
          ? `该产品风险等级为 ${plan.riskBand}，净值与收益可能波动；历史业绩不代表未来表现，不构成任何收益承诺。此授权仅用于本次${
              plan.operation === 'purchase' ? '申购' : '赎回'
            }操作。`
          : undefined
      }
      otpValue={state.otp}
      onOtpChange={flow.actions.setOtp}
      otpError={state.otpError ?? undefined}
      onConfirm={() => void flow.actions.confirm()}
      onReject={() => {
        clearRetryContext();
        flow.actions.reject();
      }}
      onClose={() => {
        clearRetryContext();
        flow.actions.onClose();
      }}
      onViewStatus={() => void flow.actions.viewStatus()}
      failedActionLabel="关闭"
      failedDescription={state.result?.reason ?? undefined}
    />
  );
}
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ConfirmResult, PlanView, TransferAccount, TransferInput, TransferRecipient } from './types';
import { parseAmountToCents } from './money';
import { OfflineError, TransferError } from './TransferService';
import type { TransferService } from './TransferService';
import {
  clearPendingApproval,
  readPendingApproval,
  savePendingApproval,
} from '../../app/viewState';

export type FlowStatus =
  | 'IDLE'
  | 'VALIDATING'
  | 'PREVIEWING'
  | 'READY_FOR_APPROVAL'
  | 'CONFIRMING'
  | 'SUCCESS'
  | 'FAILED'
  | 'UNKNOWN';

export type PlanStatus = 'idle' | 'loading' | 'ready' | 'error' | 'expired';

export interface FieldErrors {
  amount?: string;
  account?: string;
  recipient?: string;
}

export function useTransferFlow(service: TransferService, opts?: { now?: () => number }) {
  const now = opts?.now ?? (() => Date.now());
  const [accounts, setAccounts] = useState<TransferAccount[]>([]);
  const [recipients, setRecipients] = useState<TransferRecipient[]>([]);
  const [dailyRemainingCents, setDailyRemainingCents] = useState<number | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [refsLoading, setRefsLoading] = useState(false);

  const [amountText, setAmountText] = useState('');
  const [amountError, setAmountError] = useState<string | null>(null);
  const [fromAccountId, setFromAccountId] = useState<string | null>(null);
  const [recipientId, setRecipientId] = useState<string | null>(null);
  const [memo, setMemo] = useState('');

  const [flow, setFlow] = useState<FlowStatus>('IDLE');
  const [planStatus, setPlanStatus] = useState<PlanStatus>('idle');
  const [planView, setPlanView] = useState<PlanView | null>(null);
  const [planError, setPlanError] = useState<string | null>(null);
  const [approvalId, setApprovalId] = useState<string | null>(null);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [otp, setOtp] = useState('');
  const [otpError, setOtpError] = useState<string | null>(null);
  const [result, setResult] = useState<ConfirmResult | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [offline, setOffline] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [pendingApprovalId, setPendingApprovalId] = useState<string | null>(readPendingApproval);

  const previewSeqRef = useRef(0);
  const confirmingRef = useRef(false);
  const idRef = useRef<string | null>(null);
  const refsSeqRef = useRef(0);
  const refsLoadingRef = useRef(false);

  /** 加载账户 / 收款人 / 今日额度；可重复调用（初次加载 + 恢复在线后重试）。 */
  const loadReferences = useCallback(async () => {
    if (refsLoadingRef.current) return;
    refsLoadingRef.current = true;
    const seq = ++refsSeqRef.current;
    setRefsLoading(true);
    setListError(null);
    try {
      const [a, r, d] = await Promise.all([
        service.listAccounts(),
        service.listRecipients(),
        service.getDailyRemainingCents(),
      ]);
      if (seq !== refsSeqRef.current) return; // 旧响应不覆盖
      setAccounts(a);
      setRecipients(r);
      setDailyRemainingCents(d);
      setOffline(false);
    } catch (error) {
      if (seq !== refsSeqRef.current) return;
      if (error instanceof OfflineError) setOffline(true);
      else setListError('账户信息加载失败，请重试');
      // 失败时不清空已经成功展示的旧数据
    } finally {
      refsLoadingRef.current = false;
      setRefsLoading(false);
    }
  }, [service]);

  // 初次进入：加载账户 / 收款人 / 今日额度；不根据 URL 或历史预填业务字段。
  useEffect(() => {
    void loadReferences();
  }, [loadReferences]);

  const selectedAccount = useMemo(
    () => accounts.find((a) => a.id === fromAccountId) ?? null,
    [accounts, fromAccountId],
  );
  const accountPaused = selectedAccount != null && selectedAccount.status !== 'normal';

  /** 表单变化后废弃旧的 PlanView / approval，并在途的 preview/getPlanView 响应失效，回到编辑态。 */
  const invalidatePlan = useCallback(() => {
    previewSeqRef.current += 1; // 使任何在途请求（preview / getPlanView / 旧 approval）失效
    setFlow('IDLE');
    setPlanStatus('idle');
    setPlanView(null);
    setPlanError(null);
    setApprovalId(null);
    setSheetOpen(false);
    setResult(null);
    setPreviewError(null);
    setOtp('');
    setOtpError(null);
    idRef.current = null;
  }, []);

  const updateAmount = useCallback(
    (text: string) => {
      setAmountText(text);
      const parsed = parseAmountToCents(text);
      if ('error' in parsed) {
        setAmountError(parsed.error);
      } else {
        setAmountError(null);
      }
      setFieldErrors((f) => ({ ...f, amount: undefined }));
      invalidatePlan();
    },
    [invalidatePlan],
  );

  const selectAccount = useCallback(
    (id: string) => {
      setFromAccountId(id);
      setFieldErrors((f) => ({ ...f, account: undefined }));
      invalidatePlan();
    },
    [invalidatePlan],
  );

  const selectRecipient = useCallback(
    (id: string) => {
      setRecipientId(id);
      setFieldErrors((f) => ({ ...f, recipient: undefined }));
      invalidatePlan();
    },
    [invalidatePlan],
  );

  const updateMemo = useCallback(
    (text: string) => {
      setMemo(text);
      invalidatePlan();
    },
    [invalidatePlan],
  );

  const loadPlan = useCallback(
    async (id: string, seq: number) => {
      setPlanStatus('loading');
      setPlanError(null);
      setSheetOpen(true);
      setOffline(false);
      try {
        const plan = await service.getPlanView(id);
        if (seq !== previewSeqRef.current) return; // 旧预演响应不覆盖
        if (plan.expiresAtMillis < now()) {
          setPlanStatus('expired');
          setSheetOpen(false);
          setPlanView(null);
          return;
        }
        setPlanView(plan);
        setPlanStatus('ready');
        setSheetOpen(true);
      } catch (error) {
        if (seq !== previewSeqRef.current) return;
        if (error instanceof OfflineError) {
          setOffline(true);
          setPlanError('网络不可用，请恢复后重试');
        } else if (error instanceof TransferError) {
          setPlanError(error.message);
        } else {
          setPlanError('预演详情加载失败，请重试');
        }
        setPlanStatus('error');
        setSheetOpen(true);
      }
    },
    [service, now],
  );

  const preview = useCallback(async () => {
    if (confirmingRef.current || flow === 'PREVIEWING') return;

    const parsed = parseAmountToCents(amountText);
    const errors: FieldErrors = {};
    let amountCents: number | null = null;
    if ('error' in parsed) {
      errors.amount = parsed.error;
    } else {
      amountCents = parsed.cents;
    }
    if (!fromAccountId) errors.account = '请选择付款账户';
    if (!recipientId) errors.recipient = '请选择收款人';

    if (Object.keys(errors).length > 0) {
      setFieldErrors(errors);
      setFlow('IDLE');
      return; // VALIDATING 失败，不请求预演
    }

    if (amountCents == null || !fromAccountId || !recipientId) return;

    if (accountPaused) {
      setPreviewError('该账户已暂停外转，无法预演');
      setFieldErrors({ account: '该账户已暂停外转' });
      return;
    }

    const input: TransferInput = {
      amountCents,
      fromAccountId,
      recipientId,
      memo,
    };

    const seq = ++previewSeqRef.current;
    setFlow('PREVIEWING');
    setPreviewError(null);
    setOffline(false);
    setFieldErrors({});

    try {
      const res = await service.previewTransfer(input);
      if (seq !== previewSeqRef.current) return; // 多次快速预演，旧响应不覆盖
      if (res.kind === 'error') {
        setPreviewError(res.message);
        setFlow('IDLE');
        return;
      }
      setApprovalId(res.approvalId);
      idRef.current = res.approvalId;
      setFlow('READY_FOR_APPROVAL');
      await loadPlan(res.approvalId, seq);
    } catch (error) {
      if (seq !== previewSeqRef.current) return;
      if (error instanceof OfflineError) setOffline(true);
      else setPreviewError('预演失败，请稍后重试');
      setFlow('IDLE');
    }
  }, [service, flow, amountText, fromAccountId, recipientId, memo, accountPaused, loadPlan]);

  const retryPlan = useCallback(() => {
    if (approvalId) {
      void loadPlan(approvalId, previewSeqRef.current);
    }
  }, [approvalId, loadPlan]);

  const confirm = useCallback(async () => {
    if (confirmingRef.current || flow !== 'READY_FOR_APPROVAL' || !planView || !idRef.current) return;

    const isRed = planView.riskLevel === 'red';
    if (isRed && otp.length !== 6) {
      setOtpError('请输入 6 位短信验证码');
      return;
    }

    // 前端保护：确认前再次校验 PlanView 是否已过期。
    if (planView.expiresAtMillis < now()) {
      setPlanStatus('expired');
      setSheetOpen(false);
      setFlow('IDLE');
      setOtpError(null);
      return; // 不调用 confirmApproval
    }

    confirmingRef.current = true;
    setFlow('CONFIRMING');
    setOtpError(null);
    setOffline(false);

    try {
      const res = await service.confirmApproval(
        idRef.current,
        isRed ? { kind: 'red', otp } : { kind: 'yellow' },
      );
      setResult(res);
      if (res.status === 'success') {
        setFlow('SUCCESS');
        clearPendingApproval();
      } else if (res.status === 'failed') {
        setFlow('FAILED');
        clearPendingApproval();
      } else {
        setFlow('UNKNOWN');
        setPendingApprovalId(idRef.current);
        savePendingApproval(idRef.current);
      }
    } catch (error) {
      if (error instanceof OfflineError) {
        setOffline(true);
        setFlow('UNKNOWN');
        setPendingApprovalId(idRef.current);
        savePendingApproval(idRef.current);
      } else if (error instanceof TransferError && error.code === 'APPROVAL_EXPIRED') {
        // 服务端权威判定过期：进入过期态，不得误显示成「验证码不正确」。
        setPlanStatus('expired');
        setSheetOpen(false);
        setFlow('IDLE');
        setOtpError(null);
      } else if (error instanceof TransferError) {
        // 业务失败（如 OTP 校验）：回到可确认态，不清空授权。
        setOtpError(error.message);
        setFlow('READY_FOR_APPROVAL');
      } else {
        setFlow('UNKNOWN');
        setPendingApprovalId(idRef.current);
        savePendingApproval(idRef.current);
      }
    } finally {
      confirmingRef.current = false;
    }
  }, [service, flow, planView, otp, now]);

  const viewStatus = useCallback(async () => {
    const id = idRef.current ?? pendingApprovalId;
    if (!id) return;
    setOffline(false);
    try {
      const status = await service.getApprovalStatus(id);
      setResult({ status });
      if (status === 'success') {
        setFlow('SUCCESS');
        clearPendingApproval();
        setPendingApprovalId(null);
      } else if (status === 'failed') {
        setFlow('FAILED');
        clearPendingApproval();
        setPendingApprovalId(null);
      } else {
        setFlow('UNKNOWN'); // 持续未知
      }
    } catch (error) {
      if (error instanceof OfflineError) {
        setOffline(true);
        setFlow('UNKNOWN');
      } else if (error instanceof TransferError) {
        // 审批不存在/已失效等：明确提示，清除待确认，回到编辑态。
        setPreviewError(error.message);
        clearPendingApproval();
        setPendingApprovalId(null);
        setFlow('IDLE');
      } else {
        setFlow('UNKNOWN');
      }
    }
  }, [service, pendingApprovalId]);

  const reject = useCallback(() => {
    const id = idRef.current;
    if (id) {
      void service.rejectApproval(id).catch(() => undefined);
    }
    setSheetOpen(false);
    invalidatePlan();
  }, [service, invalidatePlan]);

  /** 关闭面板：按当前结果决定回归态。 */
  const onClose = useCallback(() => {
    if (flow === 'CONFIRMING') return;
    if (flow === 'SUCCESS') {
      // 完成出口：清空表单（不自动带入下一笔）
      setSheetOpen(false);
      setAmountText('');
      setAmountError(null);
      setFromAccountId(null);
      setRecipientId(null);
      setMemo('');
      setResult(null);
      setPreviewError(null);
      setOtp('');
      setOtpError(null);
      setApprovalId(null);
      setPlanView(null);
      setPlanStatus('idle');
      idRef.current = null;
      setFlow('IDLE');
      return;
    }
    if (flow === 'FAILED') {
      // 失败后回到编辑态（保留输入），不得复用原 approval_id / OTP。
      setSheetOpen(false);
      invalidatePlan();
      return;
    }
    setSheetOpen(false);
    // UNKNOWN：保留待确认入口（pendingApprovalId 仍在）；READY：取消回到编辑态。
    if (flow === 'READY_FOR_APPROVAL') {
      invalidatePlan();
      setResult(null);
    }
    // UNKNOWN 时保留 result 以便展示；其余已在上方处理。
  }, [flow, invalidatePlan]);

  const clearRecoveredPending = useCallback(() => {
    clearPendingApproval();
    setPendingApprovalId(null);
  }, []);

  return {
    state: {
      accounts,
      recipients,
      dailyRemainingCents,
      listError,
      refsLoading,
      amountText,
      amountError,
      fromAccountId,
      recipientId,
      memo,
      flow,
      planStatus,
      planView,
      planError,
      approvalId,
      sheetOpen,
      otp,
      otpError,
      result,
      previewError,
      offline,
      fieldErrors,
      pendingApprovalId,
    },
    selectedAccount,
    accountPaused,
    actions: {
      updateAmount,
      selectAccount,
      selectRecipient,
      updateMemo,
      preview,
      retryPlan,
      confirm,
      viewStatus,
      reject,
      onClose,
      setOtp,
      clearRecoveredPending,
      loadReferences,
    },
  };
}

export type TransferFlow = ReturnType<typeof useTransferFlow>;
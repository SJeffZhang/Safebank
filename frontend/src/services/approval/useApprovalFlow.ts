import { useCallback, useState, useRef } from 'react';
import type { ApprovalAuthorization, ConfirmResult, ApprovalStatus, RiskLevel } from '../transfer/types';
import { ApprovalError, GatewayOfflineError } from './shared';

export type ApprovalFlowStatus = 'IDLE' | 'PREVIEWING' | 'READY_FOR_APPROVAL' | 'CONFIRMING' | 'SUCCESS' | 'FAILED' | 'UNKNOWN';
export type PlanStatus = 'idle' | 'loading' | 'ready' | 'error' | 'expired';

export interface PlanViewBase {
  expiresAtMillis: number;
  riskLevel: RiskLevel;
}

export interface ApprovalQueryService {
  getPlanView(id: string): Promise<PlanViewBase>;
  confirmApproval(id: string, authorization: ApprovalAuthorization): Promise<ConfirmResult>;
  getApprovalStatus(id: string): Promise<ApprovalStatus>;
  rejectApproval(id: string): Promise<void>;
}

export interface PendingStore {
  read(): string | null;
  save(id: string): void;
  clear(): void;
}

export type RecoveredNotice =
  | { kind: 'success'; message: string }
  | { kind: 'failed'; message: string }
  | null;

/**
 * 卡片 / 订阅共用的审批流程 hook。
 * 预演阶段由页面通过 startPreview / isCurrent 做请求序号校验，防止旧响应覆盖新操作。
 */
export function useApprovalFlow<TPlan extends PlanViewBase>(
  query: ApprovalQueryService,
  pending: PendingStore,
  opts?: { now?: () => number; onSuccess?: () => void },
) {
  const now = opts?.now ?? (() => Date.now());
  const [flow, setFlow] = useState<ApprovalFlowStatus>('IDLE');
  const [planStatus, setPlanStatus] = useState<PlanStatus>('idle');
  const [planView, setPlanView] = useState<TPlan | null>(null);
  const [planError, setPlanError] = useState<string | null>(null);
  const [approvalId, setApprovalId] = useState<string | null>(null);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [otp, setOtp] = useState('');
  const [otpError, setOtpError] = useState<string | null>(null);
  const [result, setResult] = useState<ConfirmResult | null>(null);
  const [offline, setOffline] = useState(false);
  const [pendingApprovalId, setPendingApprovalId] = useState<string | null>(pending.read);
  /** 刷新恢复查询后的可见反馈（成功/失败），避免面板未打开时结果不可见。 */
  const [recoveredNotice, setRecoveredNotice] = useState<RecoveredNotice>(null);
  /** 待确认查询失败时的可见提示（离线/普通失败）；pending 保留，入口不消失。 */
  const [pendingQueryError, setPendingQueryError] = useState<string | null>(null);
  /** 查询进行中（防止重复点击产生并发请求）。 */
  const [querying, setQuerying] = useState(false);

  const seqRef = useRef(0);
  const confirmingRef = useRef(false);
  const idRef = useRef<string | null>(null);
  const queryingRef = useRef(false);

  /** 失效旧的 PlanView / approval / 授权，并递增序号使在途请求全部作废。 */
  const invalidate = useCallback(() => {
    seqRef.current += 1;
    setFlow('IDLE');
    setPlanStatus('idle');
    setPlanView(null);
    setPlanError(null);
    setApprovalId(null);
    setSheetOpen(false);
    setResult(null);
    setOtp('');
    setOtpError(null);
    setPendingQueryError(null);
    idRef.current = null;
  }, []);

  /** 发起一次新预演前调用：递增序号、清理旧授权与错误，返回用于 isCurrent 校验的序号。 */
  const startPreview = useCallback(() => {
    seqRef.current += 1;
    setFlow('PREVIEWING');
    setOffline(false);
    setPlanError(null);
    setPlanStatus('idle');
    setPlanView(null);
    setOtp('');
    setOtpError(null);
    setRecoveredNotice(null);
    setPendingQueryError(null);
    return seqRef.current;
  }, []);

  /** 预演请求返回后，判断请求序号是否仍为最新（旧响应据此丢弃）。 */
  const isCurrent = useCallback((seq: number) => seq === seqRef.current, []);

  const previewFailed = useCallback((message: string, isOffline: boolean) => {
    if (isOffline) setOffline(true);
    setPlanError(message);
    setPlanStatus('idle');
    setFlow('IDLE');
  }, []);

  const loadPlan = useCallback(
    async (id: string, seq: number) => {
      setPlanStatus('loading');
      setPlanError(null);
      setSheetOpen(false); // 加载中不打开任何可确认面板，避免空白
      setOffline(false);
      try {
        const plan = (await query.getPlanView(id)) as TPlan;
        if (seq !== seqRef.current) return; // 旧预演响应不覆盖
        if (plan.expiresAtMillis < now()) {
          setPlanStatus('expired');
          setPlanView(null);
          setSheetOpen(false);
          setFlow('IDLE');
          setPlanError('预演已过期，请重新预演');
          return;
        }
        setPlanView(plan);
        setPlanStatus('ready');
        setSheetOpen(true);
        setFlow('READY_FOR_APPROVAL');
      } catch (error) {
        if (seq !== seqRef.current) return;
        if (error instanceof GatewayOfflineError) {
          setOffline(true);
          setPlanError('网络不可用，请恢复后重试');
        } else if (error instanceof ApprovalError) {
          setPlanError(error.message);
        } else {
          setPlanError('预演详情加载失败，请重试');
        }
        setPlanStatus('error');
        setSheetOpen(false);
        setFlow('IDLE'); // 进入可见错误态，保留 approvalId 供重试
      }
    },
    [query, now],
  );

  /** 预演成功返回 approval_id 后，交由本 hook 拉取 PlanView（期间仍处于 PREVIEWING）。 */
  const begin = useCallback(
    (id: string) => {
      const seq = seqRef.current;
      setApprovalId(id);
      idRef.current = id;
      setFlow('PREVIEWING');
      void loadPlan(id, seq);
    },
    [loadPlan],
  );

  const retryPlan = useCallback(() => {
    if (approvalId) {
      const seq = ++seqRef.current;
      setPlanStatus('loading');
      setPlanError(null);
      void loadPlan(approvalId, seq);
    }
  }, [approvalId, loadPlan]);

  const confirm = useCallback(async () => {
    if (confirmingRef.current || flow !== 'READY_FOR_APPROVAL' || !planView || !idRef.current) return;
    const isRed = planView.riskLevel === 'red';
    if (isRed && otp.length !== 6) {
      setOtpError('请输入 6 位短信验证码');
      return;
    }
    if (planView.expiresAtMillis < now()) {
      setPlanStatus('expired');
      setSheetOpen(false);
      setFlow('IDLE');
      setOtpError(null);
      setPlanError('预演已过期，请重新预演');
      return;
    }
    confirmingRef.current = true;
    setFlow('CONFIRMING');
    setOtpError(null);
    setOffline(false);
    try {
      const res = await query.confirmApproval(idRef.current, isRed ? { kind: 'red', otp } : { kind: 'yellow' });
      setResult(res);
      if (res.status === 'success') {
        setFlow('SUCCESS');
        pending.clear();
        opts?.onSuccess?.();
      } else if (res.status === 'failed') {
        setFlow('FAILED');
        pending.clear();
      } else {
        setFlow('UNKNOWN');
        setPendingApprovalId(idRef.current);
        pending.save(idRef.current);
      }
    } catch (error) {
      if (error instanceof GatewayOfflineError) {
        setOffline(true);
        setFlow('UNKNOWN');
        setPendingApprovalId(idRef.current);
        pending.save(idRef.current);
      } else if (error instanceof ApprovalError && error.code === 'APPROVAL_EXPIRED') {
        setPlanStatus('expired');
        setSheetOpen(false);
        setFlow('IDLE');
        setOtpError(null);
        setPlanError('预演已过期，请重新预演');
      } else if (error instanceof ApprovalError) {
        setOtpError(error.message);
        setFlow('READY_FOR_APPROVAL');
      } else {
        setFlow('UNKNOWN');
        setPendingApprovalId(idRef.current);
        pending.save(idRef.current);
      }
    } finally {
      confirmingRef.current = false;
    }
  }, [query, flow, planView, otp, now, pending, opts]);

  const viewStatus = useCallback(async () => {
    if (queryingRef.current) return; // 防止重复点击产生并发查询
    const id = idRef.current ?? pendingApprovalId;
    if (!id) return;
    queryingRef.current = true;
    setQuerying(true);
    setOffline(false);
    setRecoveredNotice(null);
    setPendingQueryError(null);
    try {
      const status = await query.getApprovalStatus(id);
      setResult({ status });
      if (status === 'success') {
        setFlow('SUCCESS');
        pending.clear();
        setPendingApprovalId(null);
        setRecoveredNotice({ kind: 'success', message: '操作已完成' });
        opts?.onSuccess?.();
      } else if (status === 'failed') {
        setFlow('FAILED');
        pending.clear();
        setPendingApprovalId(null);
        setRecoveredNotice({ kind: 'failed', message: '操作未完成，请查看详情或根据提示决定下一步。' });
      } else {
        setFlow('UNKNOWN'); // 持续未知，保留 pending 与入口
      }
    } catch (error) {
      // 查询失败（离线/普通失败）：保留 pending，保留“查看状态”入口，只给出可见提示。
      if (error instanceof GatewayOfflineError) {
        setOffline(true);
        setPendingQueryError('网络不可用，查询未完成，请恢复后重试。');
      } else {
        setPendingQueryError('查询未完成，请重试。');
      }
      setFlow('UNKNOWN');
    } finally {
      queryingRef.current = false;
      setQuerying(false);
    }
  }, [query, pendingApprovalId, pending, opts]);

  const dismissNotice = useCallback(() => setRecoveredNotice(null), []);

  const reject = useCallback(() => {
    const id = idRef.current;
    if (id) void query.rejectApproval(id).catch(() => undefined);
    setSheetOpen(false);
    setRecoveredNotice(null);
    invalidate();
  }, [query, invalidate]);

  const onClose = useCallback(() => {
    if (flow === 'CONFIRMING') return;
    if (flow === 'SUCCESS' || flow === 'FAILED') {
      setSheetOpen(false);
      invalidate();
      return;
    }
    setSheetOpen(false);
    if (flow === 'READY_FOR_APPROVAL') {
      invalidate();
      setResult(null);
    }
    // UNKNOWN：保留 pendingApprovalId 入口
  }, [flow, invalidate]);

  const clearRecoveredPending = useCallback(() => {
    pending.clear();
    setPendingApprovalId(null);
  }, [pending]);

  return {
    state: {
      flow,
      planStatus,
      planView,
      planError,
      approvalId,
      sheetOpen,
      otp,
      otpError,
      result,
      offline,
      pendingApprovalId,
      recoveredNotice,
      pendingQueryError,
      querying,
    },
    actions: {
      invalidate,
      startPreview,
      isCurrent,
      previewFailed,
      begin,
      retryPlan,
      confirm,
      viewStatus,
      dismissNotice,
      reject,
      onClose,
      setOtp,
      clearRecoveredPending,
    },
  };
}

export type ApprovalFlow<TPlan extends PlanViewBase> = ReturnType<typeof useApprovalFlow<TPlan>>;
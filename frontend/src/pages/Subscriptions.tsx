import { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from '../components/Button';
import { Icon } from '../components/Icon';
import { Skeleton, SkeletonText } from '../components/Skeleton';
import { StateView } from '../components/StateView';
import { ApprovalSheet } from '../components/ApprovalSheet';
import type { ApprovalSummary } from '../components/ApprovalSheet';
import { useSubscriptionsService } from '../services/subscriptions/SubscriptionsService';
import type {
  Subscription,
  SubscriptionPlanView,
  SubscriptionSummary,
  UpcomingCharges,
} from '../services/subscriptions/types';
import { MockSubscriptionsService } from '../services/subscriptions/MockSubscriptionsService';
import type { MockSubscriptionsScenario } from '../services/subscriptions/MockSubscriptionsService';
import { useApprovalFlow } from '../services/approval/useApprovalFlow';
import { GatewayOfflineError } from '../services/approval/shared';
import { formatYuan } from '../services/transfer/money';
import { makePendingAccessors, SUBSCRIPTION_PENDING_APPROVAL_KEY } from '../app/viewState';
import { useDocumentTitle } from '../app/hooks';
import styles from '../layout/layout.module.css';
import sstyles from './Subscriptions.module.css';

const pending = makePendingAccessors(SUBSCRIPTION_PENDING_APPROVAL_KEY);

type LoadStatus = 'initial' | 'loading' | 'loaded' | 'empty' | 'error';

export function SubscriptionsPage() {
  const service = useSubscriptionsService();
  useDocumentTitle('订阅与提醒 · SafeBank');

  const [subs, setSubs] = useState<Subscription[]>([]);
  const [summary, setSummary] = useState<SubscriptionSummary | null>(null);
  const [upcoming, setUpcoming] = useState<UpcomingCharges | null>(null);
  const [status, setStatus] = useState<LoadStatus>('initial');
  const [offline, setOffline] = useState(false);
  const [reminderDrafts, setReminderDrafts] = useState<Record<string, string>>({});
  const [reminderError, setReminderError] = useState<string | null>(null);

  const seqRef = useRef(0);
  const subsRef = useRef<Subscription[]>([]);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  /** 最近一次操作的最小可重试上下文（提醒重试读取最新草稿，不复用旧时间）。 */
  const retryContextRef = useRef<
    { type: 'cancel'; subscriptionId: string } | { type: 'reminder'; subscriptionId: string } | null
  >(null);

  const reload = useCallback(
    async (seq: number): Promise<boolean> => {
      try {
        const [list, up, sum] = await Promise.all([
          service.listSubscriptions(),
          service.getUpcomingCharges(),
          service.getSummary(),
        ]);
        if (seq !== seqRef.current) return false;
        subsRef.current = list;
        setSubs(list);
        setUpcoming(up);
        setSummary(sum);
        setStatus(list.length === 0 ? 'empty' : 'loaded');
        setOffline(false);
        setRefreshError(null);
        return true;
      } catch (error) {
        if (seq !== seqRef.current) return false;
        if (error instanceof GatewayOfflineError) setOffline(true);
        setStatus(subsRef.current.length === 0 ? 'error' : 'loaded');
        return false;
      }
    },
    [service],
  );

  const approval = useApprovalFlow<SubscriptionPlanView>(service, pending, {
    onSuccess: () => {
      void reload(++seqRef.current).then((ok) => {
        if (!ok) setRefreshError('操作已完成，但最新状态刷新失败，请重试');
      });
    },
  });

  useEffect(() => {
    const seq = ++seqRef.current;
    setStatus('loading');
    void reload(seq);
  }, [reload]);

  const startCancel = useCallback(
    async (subscriptionId: string) => {
      retryContextRef.current = { type: 'cancel', subscriptionId };
      const seq = approval.actions.startPreview();
      try {
        const res = await service.prepareCancellation(subscriptionId);
        if (!approval.actions.isCurrent(seq)) return;
        if (res.kind === 'error') {
          approval.actions.previewFailed(res.message, false);
          return;
        }
        approval.actions.begin(res.approvalId);
      } catch (error) {
        if (!approval.actions.isCurrent(seq)) return;
        approval.actions.previewFailed(
          error instanceof GatewayOfflineError ? '网络不可用，请恢复后重试' : '预演失败，请稍后重试',
          error instanceof GatewayOfflineError,
        );
      }
    },
    [service, approval],
  );

  const startReminderChange = useCallback(
    async (subscriptionId: string) => {
      const sub = subs.find((s) => s.id === subscriptionId);
      const draft = reminderDrafts[subscriptionId];
      if (!sub || !draft || draft === sub.reminderTime) {
        setReminderError('请先修改提醒时间');
        return;
      }
      if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(draft)) {
        setReminderError('时间格式不正确，请使用 HH:mm');
        return;
      }
      setReminderError(null);
      retryContextRef.current = { type: 'reminder', subscriptionId };
      const seq = approval.actions.startPreview();
      try {
        const res = await service.prepareReminderChange(subscriptionId, draft);
        if (!approval.actions.isCurrent(seq)) return;
        if (res.kind === 'error') {
          approval.actions.previewFailed(res.message, false);
          return;
        }
        approval.actions.begin(res.approvalId);
      } catch (error) {
        if (!approval.actions.isCurrent(seq)) return;
        approval.actions.previewFailed(
          error instanceof GatewayOfflineError ? '网络不可用，请恢复后重试' : '预演失败，请稍后重试',
          error instanceof GatewayOfflineError,
        );
      }
    },
    [service, approval, subs, reminderDrafts],
  );

  /** 过期后「重新预演」：用保存的上下文重新 prepare；提醒修改使用最新有效草稿。 */
  const retryPreview = useCallback(() => {
    const ctx = retryContextRef.current;
    if (!ctx) return;
    if (ctx.type === 'cancel') void startCancel(ctx.subscriptionId);
    else void startReminderChange(ctx.subscriptionId);
  }, [startCancel, startReminderChange]);

  const clearRetryContext = useCallback(() => {
    retryContextRef.current = null;
  }, []);

  /**
   * 提醒草稿变化：
   * - 有在途预演 / 可确认 PlanView 时，使该预演失效（旧响应、旧 approval_id、旧面板全部作废）；
   * - 已过期时保留过期提示与「重新预演」入口（重试会读取最新草稿）；
   * - 不清除事实订阅数据，也不清除独立的待确认 pending。
   */
  const updateReminderDraft = useCallback(
    (subscriptionId: string, value: string) => {
      setReminderDrafts((d) => ({ ...d, [subscriptionId]: value }));
      setReminderError(null);
      const st = approval.state;
      if (st.flow === 'PREVIEWING' || st.flow === 'READY_FOR_APPROVAL' || st.sheetOpen) {
        approval.actions.invalidate();
      }
    },
    [approval],
  );

  const planView = approval.state.planView;
  const cancelRows: ApprovalSummary['rows'] = planView
    ? [
        { label: '取消对象', value: planView.subscriptionName, emphasis: true },
        { label: '当前扣款', value: planView.billingText },
        { label: '影响', value: planView.effectText },
        ...(planView.retentionNote ? [{ label: '提示', value: planView.retentionNote }] : []),
      ]
    : [];

  const summaryRows =
    planView?.operation === 'reminder'
      ? [
          { label: '订阅', value: planView.subscriptionName },
          { label: '修改前', value: planView.beforeReminder ?? '' },
          { label: '修改后', value: planView.afterReminder ?? '', emphasis: true },
          { label: '影响', value: planView.effectText },
        ]
      : cancelRows;

  const sheetSummary: ApprovalSummary =
    planView == null
      ? { title: '' }
      : {
          title: planView.title,
          subtitle: planView.subscriptionName,
          rows: summaryRows,
          riskNotes: planView.riskNotes,
        };

  const isMock = service instanceof MockSubscriptionsService;
  const applyScenario = (patch: Partial<MockSubscriptionsScenario>) => {
    if (isMock) (service as MockSubscriptionsService).setScenario(patch);
  };

  const retry = () => {
    const seq = ++seqRef.current;
    setStatus('loading');
    void reload(seq);
  };

  return (
    <div className={styles.content}>
      <div className={styles.intro}>
        <p className={styles.sectionLabel}>订阅与提醒</p>
        <p className={styles.introText}>管理自动扣费订阅与扣费提醒。取消与提醒修改先预演，再进入安全网关确认。</p>
      </div>

      {offline ? (
        <div className={sstyles.banner} role="status">
          <Icon name="wifi-off" size={16} />
          <span className={sstyles.bannerText}>当前离线，已展示内容不会丢失；恢复后重试。</span>
          <Button variant="secondary" onClick={retry}>
            重试
          </Button>
        </div>
      ) : null}

      {approval.state.recoveredNotice ? (
        <div
          className={`${sstyles.banner} ${approval.state.recoveredNotice.kind === 'failed' ? sstyles.alertBanner : ''}`}
          role={approval.state.recoveredNotice.kind === 'failed' ? 'alert' : 'status'}
        >
          <Icon name={approval.state.recoveredNotice.kind === 'failed' ? 'alert' : 'check'} size={16} />
          <span className={sstyles.bannerText}>{approval.state.recoveredNotice.message}</span>
          <Button variant="secondary" onClick={approval.actions.dismissNotice}>
            关闭
          </Button>
        </div>
      ) : null}

      {approval.state.pendingApprovalId && !approval.state.sheetOpen ? (
        <div className={sstyles.banner} role="status">
          <Icon name="clock" size={16} />
          <span className={sstyles.bannerText}>
            {approval.state.pendingQueryError ?? '有一项待确认的订阅操作。'}
          </span>
          <Button
            variant="secondary"
            onClick={() => void approval.actions.viewStatus()}
            disabled={approval.state.querying}
          >
            {approval.state.querying ? '查询中…' : '查看状态'}
          </Button>
        </div>
      ) : null}

      {refreshError ? (
        <div className={sstyles.banner} role="alert">
          <Icon name="alert" size={16} />
          <span className={sstyles.bannerText}>{refreshError}</span>
          <Button variant="secondary" onClick={retry}>
            重试
          </Button>
        </div>
      ) : null}

      {(approval.state.planStatus === 'error' || approval.state.planStatus === 'expired') &&
      approval.state.planError ? (
        <div className={`${sstyles.banner} ${sstyles.alertBanner}`} role="alert">
          <Icon name="alert" size={16} />
          <span className={sstyles.bannerText}>{approval.state.planError}</span>
          {approval.state.planStatus === 'error' ? (
            <Button variant="secondary" onClick={approval.actions.retryPlan}>
              重试预演详情
            </Button>
          ) : (
            <Button variant="secondary" onClick={retryPreview}>
              重新预演
            </Button>
          )}
        </div>
      ) : null}

      {approval.state.planError && approval.state.planStatus === 'idle' ? (
        <div className={`${sstyles.banner} ${sstyles.alertBanner}`} role="alert">
          <Icon name="alert" size={16} />
          <span className={sstyles.bannerText}>{approval.state.planError}</span>
        </div>
      ) : null}

      {status === 'initial' || status === 'loading' ? (
        <div className={styles.stack} role="status" aria-live="polite">
          <Skeleton height={18} />
          <Skeleton height={88} />
          <SkeletonText lines={3} />
          <p className={sstyles.demoNote}>正在加载订阅…</p>
        </div>
      ) : null}

      {status === 'error' && subs.length === 0 ? (
        <StateView state="error" title="订阅加载失败" description="请重试。" actionLabel="重试" onAction={retry} />
      ) : null}

      {status === 'empty' ? (
        <StateView state="empty" title="暂无订阅" description="你还没有任何有效订阅。" actionLabel="刷新" onAction={retry} />
      ) : null}

      {summary && upcoming ? (
        <div className={sstyles.summary}>
          <span className={sstyles.amountLabel}>年化花费</span>
          <span className={sstyles.bigAmount}>{formatYuan(summary.annualizedCents)}</span>
          <span className={sstyles.meta}>
            {summary.activeCount} 个有效订阅 · 本月支出 {formatYuan(summary.monthExpenseCents)} · 未来 7 天待扣{' '}
            {formatYuan(upcoming.upcomingTotalCents)}
          </span>
        </div>
      ) : null}

      {subs.length > 0 ? (
        <div className={styles.sectionCard}>
          <h3 className={styles.sectionLabel}>订阅列表</h3>
          {subs.map((sub) => (
            <div className={sstyles.sub} key={sub.id}>
              <span className={sstyles.icon} aria-hidden="true">
                {sub.iconLabel}
              </span>
              <div className={sstyles.subMain}>
                <span className={sstyles.subName}>
                  {sub.name}
                  {sub.idle ? <span className={sstyles.pill}>闲置</span> : null}
                </span>
                <span className={sstyles.subMeta}>
                  下次扣款 {sub.nextChargeDate} · {sub.cycleText} · 提醒 {sub.reminderTime}
                  {sub.status === 'cancelled' ? ' · 已取消' : null}
                </span>
                {sub.status === 'active' ? (
                  <div className={sstyles.subOps}>
                    <Button variant="secondary" onClick={() => void startCancel(sub.id)}>
                      取消订阅
                    </Button>
                    <input
                      className={sstyles.timeInput}
                      type="time"
                      aria-label={`${sub.name} 提醒时间`}
                      value={reminderDrafts[sub.id] ?? sub.reminderTime}
                      onChange={(e) => updateReminderDraft(sub.id, e.target.value)}
                    />
                    <Button
                      variant="secondary"
                      disabled={(reminderDrafts[sub.id] ?? sub.reminderTime) === sub.reminderTime}
                      onClick={() => void startReminderChange(sub.id)}
                    >
                      修改提醒
                    </Button>
                  </div>
                ) : null}
              </div>
              <div className={sstyles.subAmt}>
                {formatYuan(sub.amountCents)}
                <span className={sstyles.subAmtUnit}>{sub.amountUnit}</span>
              </div>
            </div>
          ))}
        </div>
      ) : null}

      {reminderError ? (
        <div className={`${sstyles.banner} ${sstyles.alertBanner}`} role="alert">
          <Icon name="alert" size={16} />
          <span className={sstyles.bannerText}>{reminderError}</span>
        </div>
      ) : null}

      {isMock ? (
        <div className={sstyles.demoPanel} aria-label="演示场景（仅演示）">
          <span className={styles.sectionLabel}>演示场景（仅演示）</span>
          <div className={sstyles.demoGrid}>
            <Button variant="secondary" onClick={() => applyScenario({ confirm: 'success', cancelRisk: 'yellow', afterUnknownStatus: 'unknown' })}>
              取消成功
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ confirm: 'failedUncertain', afterUnknownStatus: 'unknown' })}>
              取消失败
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ confirm: 'unknown', afterUnknownStatus: 'success' })}>
              取消 UNKNOWN → 成功
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ confirm: 'unknown', afterUnknownStatus: 'unknown' })}>
              持续 UNKNOWN
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ cancelRisk: 'red' })}>
              红级取消
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ planViewExpired: true })}>
              PlanView 过期
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ planViewFailOnce: true })}>
              PlanView 失败一次
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ offline: true })}>
              离线
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ offline: false })}>
              恢复在线
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ emptyList: true })}>
              空列表
            </Button>
            <Button
              variant="secondary"
              onClick={() => {
                applyScenario({ emptyList: true, listFailOnce: false });
                retry();
              }}
            >
              空列表并刷新
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ listFailOnce: true })}>
              刷新失败一次
            </Button>
            <Button
              variant="secondary"
              onClick={() => {
                applyScenario({ emptyList: false, listFailOnce: false });
                retry();
              }}
            >
              恢复列表
            </Button>
          </div>
          <p className={sstyles.demoNote}>演示场景只改变 mock 返回；接入真实服务时整块移除。</p>
        </div>
      ) : null}

      <ApprovalSheet
        open={approval.state.sheetOpen}
        approvalId={approval.state.approvalId ?? ''}
        mode={planView?.riskLevel ?? 'yellow'}
        state={
          approval.state.flow === 'CONFIRMING'
            ? 'confirming'
            : approval.state.flow === 'SUCCESS'
              ? 'success'
              : approval.state.flow === 'FAILED'
                ? 'failed'
                : approval.state.flow === 'UNKNOWN'
                  ? 'unknown'
                  : 'ready'
        }
        summary={sheetSummary}
        riskAckRequired={planView?.riskLevel === 'red'}
        otpValue={approval.state.otp}
        onOtpChange={approval.actions.setOtp}
        otpError={approval.state.otpError ?? undefined}
        onConfirm={() => void approval.actions.confirm()}
        onReject={() => {
          clearRetryContext();
          approval.actions.reject();
        }}
        onClose={() => {
          clearRetryContext();
          approval.actions.onClose();
        }}
        onViewStatus={() => void approval.actions.viewStatus()}
        failedActionLabel="关闭"
        failedDescription={approval.state.result?.reason ?? undefined}
        riskDisclosure={
          planView?.riskLevel === 'red'
            ? '取消订阅后不再自动扣款，此授权仅用于本次取消操作。'
            : undefined
        }
      />
    </div>
  );
}
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button, IconButton } from '../components/Button';
import { Icon } from '../components/Icon';
import { Skeleton, SkeletonText } from '../components/Skeleton';
import { ApprovalSheet } from '../components/ApprovalSheet';
import type { ApprovalSummary } from '../components/ApprovalSheet';
import { useSession } from '../app/SessionProvider';
import { useTheme } from '../app/ThemeProvider';
import { useDocumentTitle } from '../app/hooks';
import { PATHS } from '../app/paths';
import {
  PENDING_APPROVAL_KEYS,
  PROFILE_SECTIONS_KEY,
  makePendingAccessors,
  readAmountHidden,
  readViewState,
  writeAmountHidden,
  writeViewState,
} from '../app/viewState';
import type { PendingBusiness } from '../app/viewState';
import { useProfileService } from '../services/profile/ProfileService';
import { MockProfileService } from '../services/profile/MockProfileService';
import type { MockProfileScenario } from '../services/profile/MockProfileService';
import type { AuditEntry, NotificationItem, ProfileOverview } from '../services/profile/types';
import { GatewayOfflineError } from '../services/approval/shared';
import { useApprovalFlow } from '../services/approval/useApprovalFlow';
import { useTransferService } from '../services/transfer/TransferService';
import { useCardsService } from '../services/cards/CardsService';
import { useSubscriptionsService } from '../services/subscriptions/SubscriptionsService';
import { useWealthService } from '../services/wealth/WealthService';
import { formatCents } from '../components/AmountText';
import styles from '../layout/layout.module.css';
import p from './profile.module.css';

type LoadStatus = 'initial' | 'loading' | 'loaded' | 'empty' | 'error';

const BUSINESS_LABEL: Record<PendingBusiness, string> = {
  transfer: '转账',
  card: '卡片',
  subscription: '订阅',
  wealth: '理财',
};

const BUSINESS_PATH: Record<PendingBusiness, string> = {
  transfer: PATHS.transfer,
  card: PATHS.cards,
  subscription: PATHS.subscriptions,
  wealth: PATHS.wealth,
};

interface PendingItem {
  business: PendingBusiness;
  approvalId: string;
  risk?: 'yellow' | 'red';
  summary?: string;
  expired?: boolean;
  error?: string;
}

export function ProfilePage() {
  const navigate = useNavigate();
  const { logout, customer } = useSession();
  const { theme, toggleTheme } = useTheme();
  const profile = useProfileService();
  useDocumentTitle('个人 · SafeBank');

  const transferService = useTransferService();
  const cardService = useCardsService();
  const subscriptionService = useSubscriptionsService();
  const wealthService = useWealthService();

  const [hidden, setHidden] = useState<boolean>(readAmountHidden);
  const [overview, setOverview] = useState<ProfileOverview | null>(null);
  const [overviewStatus, setOverviewStatus] = useState<LoadStatus>('initial');
  const [overviewError, setOverviewError] = useState<string | null>(null);
  const [offline, setOffline] = useState(false);

  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [auditStatus, setAuditStatus] = useState<LoadStatus>('initial');
  const [auditError, setAuditError] = useState<string | null>(null);

  const [notifications, setNotifications] = useState<NotificationItem[]>([]);
  const [notificationsStatus, setNotificationsStatus] = useState<LoadStatus>('initial');
  const [notificationsError, setNotificationsError] = useState<string | null>(null);

  const [pendingItems, setPendingItems] = useState<PendingItem[]>([]);
  const [pendingStatus, setPendingStatus] = useState<LoadStatus>('initial');
  const [pendingError, setPendingError] = useState<string | null>(null);
  const [activeBusiness, setActiveBusiness] = useState<PendingBusiness | null>(null);

  const [sections, setSections] = useState<string[]>(() =>
    readViewState<string[]>(PROFILE_SECTIONS_KEY, ['pending', 'audit', 'notifications']),
  );
  const [notice, setNotice] = useState<string | null>(null);

  const overviewSeq = useRef(0);
  const auditSeq = useRef(0);
  const notificationsSeq = useRef(0);
  const pendingSeq = useRef(0);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => writeViewState(PROFILE_SECTIONS_KEY, sections), [sections]);

  const toggleHidden = () => {
    setHidden((prev) => {
      writeAmountHidden(!prev);
      return !prev;
    });
  };

  const toggleSection = (id: string) => {
    setSections((list) => (list.includes(id) ? list.filter((x) => x !== id) : [...list, id]));
  };

  const loadOverview = useCallback(async () => {
    const seq = ++overviewSeq.current;
    setOverviewStatus((s) => (s === 'initial' ? 'initial' : 'loading'));
    setOverviewError(null);
    try {
      const data = await profile.getOverview(customer.customerId);
      if (seq !== overviewSeq.current || !mountedRef.current) return;
      setOverview(data);
      setOverviewStatus('loaded');
      setOffline(false);
    } catch (error) {
      if (seq !== overviewSeq.current || !mountedRef.current) return;
      if (error instanceof GatewayOfflineError) setOffline(true);
      setOverviewError('账户摘要加载失败，请重试');
      setOverviewStatus(overview ? 'loaded' : 'error');
    }
  }, [profile, overview]);

  const loadAudit = useCallback(async () => {
    const seq = ++auditSeq.current;
    setAuditStatus((s) => (s === 'initial' ? 'initial' : 'loading'));
    setAuditError(null);
    try {
      const list = await profile.getAudit();
      if (seq !== auditSeq.current || !mountedRef.current) return;
      setAudit(list);
      setAuditStatus(list.length === 0 ? 'empty' : 'loaded');
      setOffline(false);
    } catch (error) {
      if (seq !== auditSeq.current || !mountedRef.current) return;
      if (error instanceof GatewayOfflineError) setOffline(true);
      setAuditError('审计链加载失败，请重试');
      setAuditStatus(audit.length === 0 ? 'error' : 'loaded');
    }
  }, [profile, audit]);

  const loadNotifications = useCallback(async () => {
    const seq = ++notificationsSeq.current;
    setNotificationsStatus((s) => (s === 'initial' ? 'initial' : 'loading'));
    setNotificationsError(null);
    try {
      const list = await profile.getNotifications();
      if (seq !== notificationsSeq.current || !mountedRef.current) return;
      setNotifications(list);
      setNotificationsStatus(list.length === 0 ? 'empty' : 'loaded');
      setOffline(false);
    } catch (error) {
      if (seq !== notificationsSeq.current || !mountedRef.current) return;
      if (error instanceof GatewayOfflineError) setOffline(true);
      setNotificationsError('通知历史加载失败，请重试');
      setNotificationsStatus(notifications.length === 0 ? 'error' : 'loaded');
    }
  }, [profile, notifications]);

  const queryFor = useCallback(
    (business: PendingBusiness) =>
      business === 'transfer'
        ? transferService
        : business === 'card'
          ? cardService
          : business === 'subscription'
            ? subscriptionService
            : wealthService,
    [transferService, cardService, subscriptionService, wealthService],
  );

  const summarize = useCallback(
    (business: PendingBusiness, planView: unknown): string => {
      if (business === 'transfer') {
        const pv = planView as { recipientName: string; amountCents: number };
        return `${pv.recipientName} ${hidden ? '••••' : formatCents(pv.amountCents)}`;
      }
      if (business === 'card') {
        const pv = planView as { cardName: string; operationLabel: string };
        return `${pv.cardName} · ${pv.operationLabel}`;
      }
      if (business === 'subscription') {
        const pv = planView as { subscriptionName: string; title: string };
        return `${pv.subscriptionName} · ${pv.title}`;
      }
      const pv = planView as { productName: string; title: string };
      return `${pv.productName} · ${pv.title}`;
    },
    [hidden],
  );

  /** 汇总四类独立 pending：不合并键，只按 approval_id 向对应业务 service 取摘要。 */
  const loadPending = useCallback(async () => {
    const seq = ++pendingSeq.current;
    const entries = (Object.keys(PENDING_APPROVAL_KEYS) as PendingBusiness[])
      .map((business) => ({ business, approvalId: makePendingAccessors(PENDING_APPROVAL_KEYS[business]).read() }))
      .filter((e): e is { business: PendingBusiness; approvalId: string } => Boolean(e.approvalId));

    if (entries.length === 0) {
      if (seq === pendingSeq.current && mountedRef.current) {
        setPendingItems([]);
        setPendingStatus('loaded');
        setPendingError(null);
      }
      return;
    }
    setPendingStatus((s) => (s === 'initial' ? 'initial' : 'loading'));
    setPendingError(null);
    const results = await Promise.all(
      entries.map(async (entry): Promise<PendingItem> => {
        try {
          const planView = await queryFor(entry.business).getPlanView(entry.approvalId);
          return {
            ...entry,
            risk: planView.riskLevel,
            summary: summarize(entry.business, planView),
            expired: planView.expiresAtMillis < Date.now(),
          };
        } catch (error) {
          return { ...entry, error: error instanceof Error ? error.message : '摘要不可用' };
        }
      }),
    );
    if (seq !== pendingSeq.current || !mountedRef.current) return;
    setPendingItems(results);
    setPendingStatus('loaded');
    if (results.some((r) => r.error)) setPendingError('部分摘要无法加载，可重试');
  }, [queryFor, summarize]);

  const refreshAll = useCallback(() => {
    void loadOverview();
    void loadPending();
  }, [loadOverview, loadPending]);

  const transferFlow = useApprovalFlow(transferService, makePendingAccessors(PENDING_APPROVAL_KEYS.transfer), {
    onSuccess: refreshAll,
  });
  const cardFlow = useApprovalFlow(cardService, makePendingAccessors(PENDING_APPROVAL_KEYS.card), {
    onSuccess: refreshAll,
  });
  const subscriptionFlow = useApprovalFlow(
    subscriptionService,
    makePendingAccessors(PENDING_APPROVAL_KEYS.subscription),
    { onSuccess: refreshAll },
  );
  const wealthFlow = useApprovalFlow(wealthService, makePendingAccessors(PENDING_APPROVAL_KEYS.wealth), {
    onSuccess: refreshAll,
  });

  useEffect(() => {
    void loadOverview();
    void loadAudit();
    void loadNotifications();
    void loadPending();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 金额隐私变化后刷新待确认摘要（摘要中的金额也需要一并隐藏）
  useEffect(() => {
    void loadPending();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hidden]);

  const flows = useMemo(
    () => ({
      transfer: transferFlow,
      card: cardFlow,
      subscription: subscriptionFlow,
      wealth: wealthFlow,
    }),
    [transferFlow, cardFlow, subscriptionFlow, wealthFlow],
  );

  const active = activeBusiness ? flows[activeBusiness] : null;

  const openPending = (item: PendingItem) => {
    if (item.expired) return;
    setActiveBusiness(item.business);
    flows[item.business].actions.begin(item.approvalId);
  };

  const closeSheet = () => {
    active?.actions.onClose();
    setActiveBusiness(null);
  };

  const sheetSummary: ApprovalSummary = (() => {
    if (!activeBusiness || !active) return { title: '' };
    const pv = active.state.planView as Record<string, unknown> | null;
    if (!pv) return { title: '' };
    if (activeBusiness === 'transfer') {
      return {
        title: String(pv.title ?? '转账确认'),
        subtitle: `${pv.recipientAccount ?? ''}`,
        amountText: formatCents(Number(pv.amountCents ?? 0)),
        rows: [
          { label: '收款人', value: String(pv.recipientName ?? '') },
          { label: '转出账户', value: String(pv.fromAccount ?? '') },
        ],
        riskNotes: (pv.riskFindings as string[]) ?? [],
      };
    }
    if (activeBusiness === 'card') {
      return {
        title: String(pv.title ?? '卡片操作'),
        subtitle: `${pv.cardName ?? ''} · ${pv.maskedNumber ?? ''}`,
        rows: [
          { label: '操作', value: String(pv.operationLabel ?? ''), emphasis: true },
          { label: '影响', value: String(pv.effectText ?? '') },
        ],
        riskNotes: (pv.riskNotes as string[]) ?? [],
      };
    }
    if (activeBusiness === 'subscription') {
      return {
        title: String(pv.title ?? '订阅操作'),
        subtitle: String(pv.subscriptionName ?? ''),
        rows: [
          { label: '当前扣款', value: String(pv.billingText ?? '') },
          { label: '影响', value: String(pv.effectText ?? '') },
        ],
        riskNotes: (pv.riskNotes as string[]) ?? [],
      };
    }
    return {
      title: String(pv.title ?? '理财操作'),
      subtitle: `${pv.productName ?? ''} · ${pv.riskBand ?? ''}`,
      rows: [
        {
          label: pv.operation === 'purchase' ? '申购金额' : '赎回份额',
          value:
            pv.operation === 'purchase'
              ? formatCents(Number(pv.amountCents ?? 0))
              : `${Number(pv.shares ?? 0).toFixed(2)} 份`,
          emphasis: true,
        },
        { label: '费用', value: String(pv.feeText ?? '') },
        { label: '到账说明', value: String(pv.arrivalText ?? '') },
      ],
      riskNotes: (pv.riskNotes as string[]) ?? [],
    };
  })();

  const signOut = () => {
    logout();
    navigate(PATHS.login, { replace: true });
  };

  const isMock = profile instanceof MockProfileService;
  const applyScenario = (patch: Partial<MockProfileScenario>) => {
    if (isMock) (profile as MockProfileService).setScenario(patch);
  };

  const user = overview?.user ?? null;
  const totalPending = pendingItems.length;

  return (
    <div className={styles.content}>
      <div className={styles.intro}>
        <p className={styles.sectionLabel}>个人</p>
        <p className={styles.introText}>账户摘要、安全与记录、账户工具。审计链只读，待确认按 approval_id 恢复。</p>
      </div>

      {offline ? (
        <div className={p.banner} role="status">
          <Icon name="wifi-off" size={16} />
          <span className={p.bannerText}>当前离线，已加载的只读记录会保留，但不代表最新数据。</span>
          <Button variant="secondary" onClick={refreshAll}>
            重试
          </Button>
        </div>
      ) : null}

      {notice ? (
        <div className={p.banner} role="status">
          <Icon name="info" size={16} />
          <span className={p.bannerText}>{notice}</span>
          <Button variant="secondary" onClick={() => setNotice(null)}>
            知道了
          </Button>
        </div>
      ) : null}

      {overviewStatus === 'initial' || (overviewStatus === 'loading' && !overview) ? (
        <div className={styles.stack} role="status" aria-live="polite">
          <Skeleton height={48} />
          <SkeletonText lines={2} />
          <p className={p.count}>正在加载账户摘要…</p>
        </div>
      ) : null}

      {overviewStatus === 'error' && !overview ? (
        <div className={`${p.banner} ${p.alertBanner}`} role="alert">
          <Icon name="alert" size={16} />
          <span className={p.bannerText}>{overviewError ?? '账户摘要加载失败'}</span>
          <Button variant="secondary" onClick={() => void loadOverview()}>
            重试
          </Button>
        </div>
      ) : null}

      {overview ? (
        <>
          <div className={p.phead}>
            <span className={p.pav} aria-hidden="true">
              {user?.avatarText ?? '客'}
            </span>
            <span className={p.pheadMain}>
              <span className={p.pname}>{user?.name ?? '未命名用户'}</span>
              <span className={p.pmeta}>
                <span className={`${p.pill} ${p.pillRisk}`}>
                  {user?.riskBandLabel ?? (overview.riskAssessment === 'missing' ? '未测评' : '风险等级未知')}
                </span>
                {user?.maskedPhone ? <span>{user.maskedPhone}</span> : <span>手机号未提供</span>}
                <span>{user?.realNameVerified ? '已实名' : '未实名'}</span>
              </span>
            </span>
            <span className={`${p.pill} ${p.pillBlue}`}>{user?.level ?? 'L2'}</span>
          </div>

          <div className={p.stats}>
            <div className={p.stat}>
              <span className={p.statValue}>{hidden ? '••••' : formatCents(overview.stats.totalAssetCents)}</span>
              <span className={p.statLabel}>总资产</span>
            </div>
            <div className={p.stat}>
              <span className={p.statValue}>{overview.stats.holdingProductCount}</span>
              <span className={p.statLabel}>持仓产品</span>
            </div>
            <div className={p.stat}>
              <span className={[p.statValue, totalPending > 0 ? p.up : ''].filter(Boolean).join(' ')}>{totalPending}</span>
              <span className={p.statLabel}>待确认</span>
            </div>
          </div>
          <p className={p.count}>资产口径与首页一致（演示数据为共享数据集，未按客户完全隔离）。</p>
          <div className={styles.row}>
            <span className={p.count}>金额隐私只影响本页展示，不改变任何业务事实。</span>
            <IconButton label={hidden ? '显示金额' : '隐藏金额'} aria-pressed={hidden} onClick={toggleHidden}>
              <Icon name={hidden ? 'eye-off' : 'eye'} size={18} />
            </IconButton>
          </div>

          <div className={styles.sectionCard}>
            <div className={p.sectionHead}>
              <span className={styles.sectionLabel}>安全与记录</span>
              {totalPending > 0 ? <span className={p.badge}>{totalPending}</span> : null}
            </div>

            {totalPending > 0 ? (
              <button
                type="button"
                className={p.toolRow}
                aria-expanded={sections.includes('pending')}
                onClick={() => toggleSection('pending')}
              >
                <span className={p.toolMain}>
                  <span className={p.toolTitle}>待确认审批</span>
                  <span className={p.toolSub}>共 {totalPending} 项 · 按 approval_id 恢复</span>
                </span>
                <Icon name="chevron-right" size={16} />
              </button>
            ) : (
              <p className={p.count}>当前没有待确认审批。</p>
            )}

            {totalPending > 0 && sections.includes('pending') ? (
              <div>
                {pendingStatus === 'loading' ? <SkeletonText lines={2} /> : null}
                {pendingError ? (
                  <div className={`${p.banner} ${p.alertBanner}`} role="alert">
                    <Icon name="alert" size={16} />
                    <span className={p.bannerText}>{pendingError}</span>
                    <Button variant="secondary" onClick={() => void loadPending()}>
                      重试
                    </Button>
                  </div>
                ) : null}
                {pendingItems.map((item) => (
                  <div key={`${item.business}-${item.approvalId}`}>
                    <button
                      type="button"
                      className={p.apRow}
                      disabled={Boolean(item.expired)}
                      onClick={() => openPending(item)}
                    >
                      <span className={p.apMain}>
                        <span className={p.apTitle}>
                          {BUSINESS_LABEL[item.business]} · {item.summary ?? item.error ?? '摘要不可用'}
                        </span>
                        <span className={p.apSub}>
                          {item.approvalId}
                          {item.expired ? ' · 已过期' : item.error ? ' · 摘要加载失败' : ''}
                        </span>
                      </span>
                      <span className={`${p.pill} ${item.risk === 'red' ? p.pillRed : p.pillRisk}`}>
                        {item.risk === 'red' ? '红级' : item.risk === 'yellow' ? '黄级' : '未知'}
                      </span>
                    </button>
                    {item.expired ? (
                      <div className={p.banner}>
                        <Icon name="clock" size={16} />
                        <span className={p.bannerText}>该审批已过期，需要回到原业务页重新预演。</span>
                        <Button variant="secondary" onClick={() => navigate(BUSINESS_PATH[item.business])}>
                          回到{BUSINESS_LABEL[item.business]}页
                        </Button>
                      </div>
                    ) : null}
                  </div>
                ))}
              </div>
            ) : null}

            <button
              type="button"
              className={p.toolRow}
              aria-expanded={sections.includes('audit')}
              onClick={() => toggleSection('audit')}
            >
              <span className={p.toolMain}>
                <span className={p.toolTitle}>审计链 · append-only</span>
                <span className={p.toolSub}>只读记录，不提供编辑、删除或清空</span>
              </span>
              <Icon name="chevron-right" size={16} />
            </button>
            {sections.includes('audit') ? (
              <div>
                {auditStatus === 'loading' && audit.length === 0 ? <SkeletonText lines={2} /> : null}
                {auditStatus === 'error' ? (
                  <div className={`${p.banner} ${p.alertBanner}`} role="alert">
                    <Icon name="alert" size={16} />
                    <span className={p.bannerText}>{auditError ?? '审计链加载失败'}</span>
                    <Button variant="secondary" onClick={() => void loadAudit()}>
                      重试
                    </Button>
                  </div>
                ) : null}
                {auditStatus === 'empty' ? (
                  <p className={p.count}>暂无审计记录。</p>
                ) : (
                  audit.map((entry) => (
                    <div className={p.audRow} key={entry.id}>
                      <span className={p.audMain}>
                        <span className={`${p.pill} ${entry.tone === 'red' ? p.pillRed : p.pillBlue}`}>{entry.result}</span>{' '}
                        {entry.planId} · {entry.title}
                        <span className={p.apSub}>{entry.time}</span>
                      </span>
                      <code className={p.audHash}>{entry.hashMasked}</code>
                    </div>
                  ))
                )}
              </div>
            ) : null}

            <button
              type="button"
              className={p.toolRow}
              aria-expanded={sections.includes('notifications')}
              onClick={() => toggleSection('notifications')}
            >
              <span className={p.toolMain}>
                <span className={p.toolTitle}>通知历史 · 拦截与订阅同列</span>
                <span className={p.toolSub}>点击进入对应业务页</span>
              </span>
              <Icon name="chevron-right" size={16} />
            </button>
            {sections.includes('notifications') ? (
              <div>
                {notificationsStatus === 'loading' && notifications.length === 0 ? <SkeletonText lines={2} /> : null}
                {notificationsStatus === 'error' ? (
                  <div className={`${p.banner} ${p.alertBanner}`} role="alert">
                    <Icon name="alert" size={16} />
                    <span className={p.bannerText}>{notificationsError ?? '通知历史加载失败'}</span>
                    <Button variant="secondary" onClick={() => void loadNotifications()}>
                      重试
                    </Button>
                  </div>
                ) : null}
                {notificationsStatus === 'empty' ? (
                  <p className={p.count}>暂无通知。</p>
                ) : (
                  notifications.map((item) => (
                    <button
                      key={item.id}
                      type="button"
                      className={p.ntfRow}
                      disabled={!item.targetPath}
                      onClick={() => {
                        if (item.targetPath) navigate(item.targetPath);
                      }}
                    >
                      <span className={`${p.pill} ${item.kind === 'block' ? p.pillRisk : p.pillBlue}`}>
                        {item.kindLabel}
                      </span>
                      <span className={p.ntfTitle}>
                        {item.title}
                        {!item.targetPath ? <span className={p.apSub}>{item.targetNote ?? '暂无对应业务页'}</span> : null}
                      </span>
                      <span className={p.ntfTime}>{item.time}</span>
                    </button>
                  ))
                )}
              </div>
            ) : null}
          </div>

          <div className={styles.sectionCard}>
            <div className={p.sectionHead}>
              <span className={styles.sectionLabel}>账户与工具</span>
              <span className={p.count}>3 项</span>
            </div>
            <button type="button" className={p.toolRow} onClick={() => navigate(PATHS.subscriptions)}>
              <span className={p.toolMain}>
                <span className={p.toolTitle}>订阅与提醒</span>
                <span className={p.toolSub}>{overview.tools.subscriptionNote}</span>
              </span>
              <Icon name="chevron-right" size={16} />
            </button>
            <button type="button" className={p.toolRow} onClick={() => navigate(PATHS.cards)}>
              <span className={p.toolMain}>
                <span className={p.toolTitle}>卡片管理</span>
                <span className={p.toolSub}>{overview.tools.cardNote}</span>
              </span>
              <Icon name="chevron-right" size={16} />
            </button>
            <button
              type="button"
              className={p.toolRow}
              onClick={() => setNotice('风险测评问卷：原型尚未开放重测。')}
            >
              <span className={p.toolMain}>
                <span className={p.toolTitle}>风险测评</span>
                <span className={p.toolSub}>
                  {overview.riskAssessment === 'valid'
                    ? '当前测评有效（来自权威 service）'
                    : overview.riskAssessment === 'expired'
                      ? '测评已过期 · 原型尚未开放重测'
                      : '尚未测评 · 原型尚未开放重测'}
                </span>
              </span>
              <Icon name="chevron-right" size={16} />
            </button>
            <button type="button" className={p.toolRow} onClick={toggleTheme}>
              <span className={p.toolMain}>
                <span className={p.toolTitle}>外观主题</span>
                <span className={p.toolSub}>当前为{theme === 'dark' ? '深色' : '浅色'} · 属于偏好</span>
              </span>
              <Icon name="chevron-right" size={16} />
            </button>
          </div>
        </>
      ) : null}

      <div className={styles.sectionCard}>
        <p className={styles.sectionLabel}>退出登录</p>
        <p className={styles.hint}>
          清除演示会话、各业务视图状态、全部待确认入口与 Mock 记录；保留主题、金额隐私与已访问标记。
        </p>
        <Button variant="danger" fullWidth onClick={signOut}>
          退出登录
        </Button>
      </div>

      {isMock ? (
        <div className={p.demoPanel} aria-label="演示场景（仅演示）">
          <span className={styles.sectionLabel}>演示场景（仅演示）</span>
          <div className={p.demoGrid}>
            <Button
              variant="secondary"
              onClick={() => {
                applyScenario({ overviewFailOnce: true });
                void loadOverview();
              }}
            >
              摘要失败一次
            </Button>
            <Button
              variant="secondary"
              onClick={() => {
                applyScenario({ auditFailOnce: true, emptyAudit: false });
                void loadAudit();
              }}
            >
              审计失败一次
            </Button>
            <Button
              variant="secondary"
              onClick={() => {
                applyScenario({ notificationsFailOnce: true, emptyNotifications: false });
                void loadNotifications();
              }}
            >
              通知失败一次
            </Button>
            <Button
              variant="secondary"
              onClick={() => {
                applyScenario({ emptyAudit: true });
                void loadAudit();
              }}
            >
              空审计
            </Button>
            <Button
              variant="secondary"
              onClick={() => {
                applyScenario({ emptyNotifications: true });
                void loadNotifications();
              }}
            >
              空通知
            </Button>
            <Button
              variant="secondary"
              onClick={() => {
                applyScenario({ missingUser: true });
                void loadOverview();
              }}
            >
              资料缺失
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ riskAssessment: 'missing' })}>
              未测评
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ offline: true })}>
              离线
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ offline: false })}>
              恢复在线
            </Button>
          </div>
          <p className={p.demoNote}>演示场景只改变 mock 返回；接入真实服务时整块移除。</p>
        </div>
      ) : null}

      <ApprovalSheet
        open={Boolean(active?.state.sheetOpen)}
        approvalId={active?.state.approvalId ?? ''}
        mode={(active?.state.planView as { riskLevel?: 'yellow' | 'red' } | null)?.riskLevel ?? 'yellow'}
        state={
          active?.state.flow === 'CONFIRMING'
            ? 'confirming'
            : active?.state.flow === 'SUCCESS'
              ? 'success'
              : active?.state.flow === 'FAILED'
                ? 'failed'
                : active?.state.flow === 'UNKNOWN'
                  ? 'unknown'
                  : 'ready'
        }
        summary={sheetSummary}
        riskAckRequired={(active?.state.planView as { riskLevel?: string } | null)?.riskLevel === 'red'}
        otpValue={active?.state.otp ?? ''}
        onOtpChange={active?.actions.setOtp}
        otpError={active?.state.otpError ?? undefined}
        onConfirm={() => void active?.actions.confirm()}
        onReject={() => {
          active?.actions.reject();
          setActiveBusiness(null);
        }}
        onClose={closeSheet}
        onViewStatus={() => void active?.actions.viewStatus()}
        failedActionLabel="关闭"
        failedDescription={active?.state.result?.reason ?? undefined}
      />
    </div>
  );
}
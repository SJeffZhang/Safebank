import { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from '../components/Button';
import { Icon } from '../components/Icon';
import { Skeleton, SkeletonText } from '../components/Skeleton';
import { StateView } from '../components/StateView';
import { ApprovalSheet } from '../components/ApprovalSheet';
import type { ApprovalSummary } from '../components/ApprovalSheet';
import { useCardsService } from '../services/cards/CardsService';
import type { CardInfo, CardPlanView, CardOperationKind } from '../services/cards/types';
import { MockCardsService } from '../services/cards/MockCardsService';
import type { MockCardsScenario } from '../services/cards/MockCardsService';
import { useApprovalFlow } from '../services/approval/useApprovalFlow';
import { GatewayOfflineError } from '../services/approval/shared';
import { makePendingAccessors, CARD_PENDING_APPROVAL_KEY } from '../app/viewState';
import { useDocumentTitle } from '../app/hooks';
import styles from '../layout/layout.module.css';
import cstyles from './Cards.module.css';

const pending = makePendingAccessors(CARD_PENDING_APPROVAL_KEY);

type LoadStatus = 'initial' | 'loading' | 'loaded' | 'empty' | 'error';

function statusBadge(card: CardInfo): { text: string; cls: string } {
  if (card.status === 'reportedLost') return { text: '已挂失', cls: cstyles.lostBadge };
  if (card.status === 'locked') return { text: '已锁定', cls: cstyles.lockBadge };
  if (card.expiringSoon) return { text: '即将到期', cls: cstyles.warnBadge };
  return { text: '正常', cls: cstyles.normalBadge };
}

export function CardsPage() {
  const service = useCardsService();
  useDocumentTitle('卡片 · SafeBank');

  const [cards, setCards] = useState<CardInfo[]>([]);
  const [status, setStatus] = useState<LoadStatus>('initial');
  const [offline, setOffline] = useState(false);

  const seqRef = useRef(0);
  const cardsRef = useRef<CardInfo[]>([]);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  /** 最近一次操作的最小可重试上下文，供过期后「重新预演」使用。 */
  const retryContextRef = useRef<{ cardId: string; operation: CardOperationKind } | null>(null);

  const reload = useCallback(
    async (seq: number): Promise<boolean> => {
      try {
        const list = await service.listCards();
        if (seq !== seqRef.current) return false;
        cardsRef.current = list;
        setCards(list);
        setStatus(list.length === 0 ? 'empty' : 'loaded');
        setOffline(false);
        setRefreshError(null);
        return true;
      } catch (error) {
        if (seq !== seqRef.current) return false;
        if (error instanceof GatewayOfflineError) setOffline(true);
        setStatus(cardsRef.current.length === 0 ? 'error' : 'loaded');
        return false;
      }
    },
    [service],
  );

  const approval = useApprovalFlow<CardPlanView>(service, pending, {
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

  const startOperation = useCallback(
    async (cardId: string, operation: CardOperationKind) => {
      retryContextRef.current = { cardId, operation };
      const seq = approval.actions.startPreview();
      try {
        const res = await service.prepareCardOperation(cardId, operation);
        if (!approval.actions.isCurrent(seq)) return; // 旧响应丢弃
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

  /** 过期后「重新预演」：用保存的上下文重新 prepare，获取新的 approval_id 与 PlanView。 */
  const retryPreview = useCallback(() => {
    const ctx = retryContextRef.current;
    if (!ctx) return;
    void startOperation(ctx.cardId, ctx.operation);
  }, [startOperation]);

  const clearRetryContext = useCallback(() => {
    retryContextRef.current = null;
  }, []);

  const planView = approval.state.planView;
  const summary: ApprovalSummary =
    planView == null
      ? { title: '' }
      : {
          title: planView.title,
          subtitle: `${planView.cardName} · ${planView.maskedNumber}`,
          rows: [
            { label: '操作', value: planView.operationLabel, emphasis: true },
            { label: '影响', value: planView.effectText },
          ],
          riskNotes: planView.riskNotes,
        };

  const isMock = service instanceof MockCardsService;
  const applyScenario = (patch: Partial<MockCardsScenario>) => {
    if (isMock) (service as MockCardsService).setScenario(patch);
  };

  const retry = () => {
    const seq = ++seqRef.current;
    setStatus('loading');
    void reload(seq);
  };

  return (
    <div className={styles.content}>
      <div className={styles.intro}>
        <p className={styles.sectionLabel}>卡片与安全</p>
        <p className={styles.introText}>管理卡片锁定、线上支付与挂失。所有写操作先预演，再进入安全网关确认。</p>
      </div>

      {offline ? (
        <div className={cstyles.banner} role="status">
          <Icon name="wifi-off" size={16} />
          <span className={cstyles.bannerText}>当前离线，已展示内容不会丢失；恢复后重试。</span>
          <Button variant="secondary" onClick={retry}>
            重试
          </Button>
        </div>
      ) : null}

      {approval.state.recoveredNotice ? (
        <div
          className={`${cstyles.banner} ${approval.state.recoveredNotice.kind === 'failed' ? cstyles.alertBanner : ''}`}
          role={approval.state.recoveredNotice.kind === 'failed' ? 'alert' : 'status'}
        >
          <Icon name={approval.state.recoveredNotice.kind === 'failed' ? 'alert' : 'check'} size={16} />
          <span className={cstyles.bannerText}>{approval.state.recoveredNotice.message}</span>
          <Button variant="secondary" onClick={approval.actions.dismissNotice}>
            关闭
          </Button>
        </div>
      ) : null}

      {approval.state.pendingApprovalId && !approval.state.sheetOpen ? (
        <div className={cstyles.banner} role="status">
          <Icon name="clock" size={16} />
          <span className={cstyles.bannerText}>
            {approval.state.pendingQueryError ?? '有一项待确认的卡片操作。'}
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
        <div className={cstyles.banner} role="alert">
          <Icon name="alert" size={16} />
          <span className={cstyles.bannerText}>{refreshError}</span>
          <Button variant="secondary" onClick={retry}>
            重试
          </Button>
        </div>
      ) : null}

      {(approval.state.planStatus === 'error' || approval.state.planStatus === 'expired') &&
      approval.state.planError ? (
        <div className={`${cstyles.banner} ${cstyles.alertBanner}`} role="alert">
          <Icon name="alert" size={16} />
          <span className={cstyles.bannerText}>{approval.state.planError}</span>
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
        <div className={`${cstyles.banner} ${cstyles.alertBanner}`} role="alert">
          <Icon name="alert" size={16} />
          <span className={cstyles.bannerText}>{approval.state.planError}</span>
        </div>
      ) : null}

      {status === 'initial' || status === 'loading' ? (
        <div className={styles.stack} role="status" aria-live="polite">
          <Skeleton height={18} />
          <Skeleton height={88} />
          <SkeletonText lines={2} />
          <p className={cstyles.demoNote}>正在加载卡片…</p>
        </div>
      ) : null}

      {status === 'error' && cards.length === 0 ? (
        <StateView state="error" title="卡片加载失败" description="请重试。" actionLabel="重试" onAction={retry} />
      ) : null}

      {status === 'empty' ? (
        <StateView state="empty" title="暂无卡片" description="你还没有任何卡片记录。" actionLabel="刷新" onAction={retry} />
      ) : null}

      {cards.length > 0 ? (
        <div className={styles.stack}>
          {cards.map((card) => {
            const badge = statusBadge(card);
            return (
              <div className={cstyles.card} key={card.id} data-sb-sheen>
                <div className={cstyles.cardHead}>
                  <span className={cstyles.cardTitle}>{card.name}</span>
                  <span className={`${cstyles.statusBadge} ${badge.cls}`}>{badge.text}</span>
                </div>
                <span className={cstyles.cardNumber}>{card.maskedNumber}</span>
                <div className={cstyles.statusRow}>
                  <span>有效期 {card.expiry}</span>
                  <span>线上支付 {card.onlinePayEnabled ? '已开启' : '已关闭'}</span>
                  {card.expiringSoon ? <span>近期到期，建议留意换卡</span> : null}
                </div>
                <div className={cstyles.ops}>
                  {card.availableOperations.map((op) => (
                    <Button
                      key={op}
                      variant={op === 'reportLost' ? 'danger' : 'secondary'}
                      className={cstyles.opBtn}
                      onClick={() => void startOperation(card.id, op)}
                    >
                      {op === 'lock'
                        ? '锁定'
                        : op === 'unlock'
                          ? '解锁'
                          : op === 'onlinePayOn'
                            ? '开启线上支付'
                            : op === 'onlinePayOff'
                              ? '关闭线上支付'
                              : '挂失'}
                    </Button>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      ) : null}

      {isMock ? (
        <div className={cstyles.demoPanel} aria-label="演示场景（仅演示）">
          <span className={styles.sectionLabel}>演示场景（仅演示）</span>
          <div className={cstyles.demoGrid}>
            <Button variant="secondary" onClick={() => applyScenario({ confirm: 'success', afterUnknownStatus: 'unknown' })}>
              成功
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ confirm: 'failedUncertain', afterUnknownStatus: 'unknown' })}>
              失败
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ confirm: 'unknown', afterUnknownStatus: 'success' })}>
              UNKNOWN → 成功
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ confirm: 'unknown', afterUnknownStatus: 'failed' })}>
              UNKNOWN → 失败
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ confirm: 'unknown', afterUnknownStatus: 'unknown' })}>
              持续 UNKNOWN
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ planViewFailOnce: true })}>
              PlanView 失败一次
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ planViewExpired: true })}>
              PlanView 过期
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
          <p className={cstyles.demoNote}>演示场景只改变 mock 返回；接入真实服务时整块移除。</p>
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
        summary={summary}
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
          planView?.operationLabel === '挂失卡片'
            ? '挂失后卡片立即停止使用，需要重新申领新卡。此授权仅用于本次挂失操作。'
            : undefined
        }
      />
    </div>
  );
}
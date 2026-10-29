import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button, IconButton } from '../components/Button';
import { Icon } from '../components/Icon';
import { Skeleton, SkeletonText } from '../components/Skeleton';
import { StateView } from '../components/StateView';
import { SegmentedControl } from '../components/SegmentedControl';
import { useWealthService } from '../services/wealth/WealthService';
import { MockWealthService } from '../services/wealth/MockWealthService';
import type { MockWealthScenario } from '../services/wealth/MockWealthService';
import type {
  ProductCategory,
  TrendPeriod,
  WealthHolding,
  WealthOverview,
  WealthProduct,
} from '../services/wealth/types';
import { GatewayOfflineError } from '../services/approval/shared';
import { useWealthApproval, WealthApprovalSheet } from './wealth/useWealthApproval';
import {
  formatAmount,
  formatAmountSigned,
  formatNetValue,
  formatPercent,
  formatShares,
  parseYuanToCents,
} from './wealth/format';
import {
  readAmountHidden,
  readViewState,
  WEALTH_SECTIONS_KEY,
  WEALTH_TAB_KEY,
  writeAmountHidden,
  writeViewState,
} from '../app/viewState';
import { useDocumentTitle } from '../app/hooks';
import { PATHS } from '../app/paths';
import styles from '../layout/layout.module.css';
import w from './wealth/wealth.module.css';

type LoadStatus = 'initial' | 'loading' | 'loaded' | 'empty' | 'error';
type FilterCategory = 'all' | 'holding' | 'current' | 'steady' | 'fund';

const CATEGORY_OPTIONS: Array<{ value: FilterCategory; label: string }> = [
  { value: 'all', label: '全部' },
  { value: 'holding', label: '持仓' },
  { value: 'current', label: '活期' },
  { value: 'steady', label: '稳健' },
  { value: 'fund', label: '基金' },
];

const PERIODS: TrendPeriod[] = ['1M', '3M', '1Y'];

function riskClass(band: string): string {
  return band === 'R1' ? w.riskR1 : band === 'R2' ? w.riskR2 : band === 'R3' ? w.riskR3 : w.riskR4;
}

export function WealthPage() {
  const service = useWealthService();
  const navigate = useNavigate();
  useDocumentTitle('理财 · SafeBank');

  const [tab, setTab] = useState<string>(() => readViewState(WEALTH_TAB_KEY, 'holdings'));
  const [hidden, setHidden] = useState<boolean>(readAmountHidden);
  const [notice, setNotice] = useState<string | null>(null);

  const [overview, setOverview] = useState<WealthOverview | null>(null);
  const [holdings, setHoldings] = useState<WealthHolding[]>([]);
  const [assetsStatus, setAssetsStatus] = useState<LoadStatus>('initial');
  const [assetsError, setAssetsError] = useState<string | null>(null);
  const [offline, setOffline] = useState(false);

  const [products, setProducts] = useState<WealthProduct[]>([]);
  const [productsStatus, setProductsStatus] = useState<LoadStatus>('initial');
  const [productsError, setProductsError] = useState<string | null>(null);

  const [period, setPeriod] = useState<TrendPeriod>('1M');
  const [category, setCategory] = useState<FilterCategory>('all');
  const [sections, setSections] = useState<string[]>(() =>
    readViewState<string[]>(WEALTH_SECTIONS_KEY, ['products', 'newcomer']),
  );

  const [selectedProductId, setSelectedProductId] = useState<string | null>(null);
  const [amountText, setAmountText] = useState('');
  const [amountError, setAmountError] = useState<string | null>(null);

  const assetsSeqRef = useRef(0);
  const productsSeqRef = useRef(0);
  const trendSeqRef = useRef(0);
  const periodRef = useRef<TrendPeriod>(period);
  const holdingsRef = useRef<WealthHolding[]>([]);

  useEffect(() => writeViewState(WEALTH_TAB_KEY, tab), [tab]);
  useEffect(() => writeViewState(WEALTH_SECTIONS_KEY, sections), [sections]);

  const toggleHidden = () => {
    setHidden((prev) => {
      writeAmountHidden(!prev);
      return !prev;
    });
  };

  /** 重新获取权威总览与持仓；返回是否成功（供「操作成功但刷新失败」区分事实）。 */
  const refetchAssets = useCallback(async (): Promise<boolean> => {
    const seq = ++assetsSeqRef.current;
    try {
      const [ov, list] = await Promise.all([service.getOverview(periodRef.current), service.getHoldings()]);
      if (seq !== assetsSeqRef.current) return false;
      setOverview(ov);
      setHoldings(list);
      holdingsRef.current = list;
      setAssetsStatus(list.length === 0 ? 'empty' : 'loaded');
      setAssetsError(null);
      setOffline(false);
      return true;
    } catch (error) {
      if (seq !== assetsSeqRef.current) return false;
      if (error instanceof GatewayOfflineError) setOffline(true);
      setAssetsStatus(holdingsRef.current.length === 0 ? 'error' : 'loaded');
      setAssetsError('资产加载失败，请重试');
      return false;
    }
  }, [service]);

  const approval = useWealthApproval(service, refetchAssets);

  const loadAssets = useCallback(
    async (mode: 'initial' | 'retry') => {
      const seq = ++assetsSeqRef.current;
      if (mode === 'initial') setAssetsStatus('initial');
      setAssetsError(null);
      try {
        const [ov, list] = await Promise.all([service.getOverview(periodRef.current), service.getHoldings()]);
        if (seq !== assetsSeqRef.current) return;
        setOverview(ov);
        setHoldings(list);
        holdingsRef.current = list;
        setAssetsStatus(list.length === 0 ? 'empty' : 'loaded');
        setOffline(false);
      } catch (error) {
        if (seq !== assetsSeqRef.current) return;
        if (error instanceof GatewayOfflineError) setOffline(true);
        setAssetsError('资产加载失败，请重试');
        setAssetsStatus(holdingsRef.current.length === 0 ? 'error' : 'loaded');
      }
    },
    [service],
  );

  const loadProducts = useCallback(async () => {
    const seq = ++productsSeqRef.current;
    setProductsStatus((s) => (s === 'initial' ? 'initial' : 'loading'));
    setProductsError(null);
    try {
      const list = await service.getProducts();
      if (seq !== productsSeqRef.current) return;
      setProducts(list);
      setProductsStatus('loaded');
    } catch (error) {
      if (seq !== productsSeqRef.current) return;
      if (error instanceof GatewayOfflineError) setOffline(true);
      setProductsError('产品加载失败，请重试');
      setProductsStatus('error');
    }
  }, [service]);

  useEffect(() => {
    void loadAssets('initial');
    void loadProducts();
  }, [loadAssets, loadProducts]);

  /** 趋势周期切换：重新获取总览，旧周期响应不得覆盖新周期。 */
  const changePeriod = (next: TrendPeriod) => {
    if (next === period) return;
    setPeriod(next);
    periodRef.current = next;
    const seq = ++trendSeqRef.current;
    void service
      .getOverview(next)
      .then((ov) => {
        if (seq !== trendSeqRef.current) return;
        setOverview(ov);
      })
      .catch(() => undefined);
  };

  const toggleSection = (id: string) => {
    setSections((list) => (list.includes(id) ? list.filter((x) => x !== id) : [...list, id]));
  };

  const holdingProductIds = useMemo(() => new Set(holdings.map((h) => h.productId)), [holdings]);

  const visibleProducts = useMemo(() => {
    return products.filter((p) => {
      if (category === 'all') return true;
      if (category === 'holding') return holdingProductIds.has(p.productId);
      return p.category === (category as ProductCategory);
    });
  }, [products, category, holdingProductIds]);

  const selectedProduct = products.find((p) => p.productId === selectedProductId) ?? null;
  const planState = approval.approval.state;

  const submitPurchase = () => {
    if (!selectedProduct) return;
    const parsed = parseYuanToCents(amountText);
    if ('error' in parsed) {
      setAmountError(parsed.error);
      return;
    }
    if (parsed.cents < selectedProduct.minPurchaseCents) {
      setAmountError(`低于起购金额（${formatAmount(selectedProduct.minPurchaseCents, false)}）`);
      return;
    }
    if (overview && parsed.cents > overview.availableBalanceCents) {
      setAmountError('可用余额不足');
      return;
    }
    setAmountError(null);
    void approval.startPurchase(selectedProduct.productId, parsed.cents);
  };

  const isMock = service instanceof MockWealthService;
  const applyScenario = (patch: Partial<MockWealthScenario>) => {
    if (isMock) (service as MockWealthService).setScenario(patch);
  };

  return (
    <div className={styles.content}>
      <div className={styles.intro}>
        <p className={styles.sectionLabel}>理财</p>
        <p className={styles.introText}>查看持仓与收益，或发现适合自己的产品。数据为演示用 mock，不代表真实收益。</p>
      </div>

      <div className={styles.row}>
        <SegmentedControl
          label="理财视图"
          options={[
            { value: 'holdings', label: `持仓${overview ? ` ${overview.holdingsCount}` : ''}` },
            { value: 'discover', label: '发现' },
          ]}
          value={tab}
          onChange={setTab}
          panelId="wealth-panel"
        />
        <Button variant="secondary" onClick={() => navigate(PATHS.wealthHoldings)}>
          全部持仓
        </Button>
      </div>

      {offline ? (
        <div className={w.banner} role="status">
          <Icon name="wifi-off" size={16} />
          <span className={w.bannerText}>当前离线，已展示内容不会丢失；恢复后重试。</span>
          <Button variant="secondary" onClick={() => void loadAssets('retry')}>
            重试
          </Button>
        </div>
      ) : null}

      {assetsError && overview ? (
        <div className={`${w.banner} ${w.alertBanner}`} role="alert">
          <Icon name="alert" size={16} />
          <span className={w.bannerText}>{assetsError}</span>
          <Button variant="secondary" onClick={() => void loadAssets('retry')}>
            重试
          </Button>
        </div>
      ) : null}

      {notice ? (
        <div className={w.banner} role="status">
          <Icon name="info" size={16} />
          <span className={w.bannerText}>{notice}</span>
          <Button variant="secondary" onClick={() => setNotice(null)}>
            知道了
          </Button>
        </div>
      ) : null}

      {planState.recoveredNotice ? (
        <div
          className={`${w.banner} ${planState.recoveredNotice.kind === 'failed' ? w.alertBanner : ''}`}
          role={planState.recoveredNotice.kind === 'failed' ? 'alert' : 'status'}
        >
          <Icon name={planState.recoveredNotice.kind === 'failed' ? 'alert' : 'check'} size={16} />
          <span className={w.bannerText}>{planState.recoveredNotice.message}</span>
          <Button variant="secondary" onClick={approval.approval.actions.dismissNotice}>
            关闭
          </Button>
        </div>
      ) : null}

      {planState.planStatus === 'error' || planState.planStatus === 'expired' ? (
        planState.planError ? (
          <div className={`${w.banner} ${w.alertBanner}`} role="alert">
            <Icon name="alert" size={16} />
            <span className={w.bannerText}>{planState.planError}</span>
            {planState.planStatus === 'error' ? (
              <Button variant="secondary" onClick={planState && approval.approval.actions.retryPlan}>
                重试预演详情
              </Button>
            ) : (
              <Button variant="secondary" onClick={approval.retryPreview}>
                重新预演
              </Button>
            )}
          </div>
        ) : null
      ) : planState.planError && planState.planStatus === 'idle' ? (
        <div className={`${w.banner} ${w.alertBanner}`} role="alert">
          <Icon name="alert" size={16} />
          <span className={w.bannerText}>{planState.planError}</span>
        </div>
      ) : null}

      {approval.refreshError ? (
        <div className={w.banner} role="alert">
          <Icon name="alert" size={16} />
          <span className={w.bannerText}>{approval.refreshError}</span>
          <Button variant="secondary" onClick={() => void refetchAssets()}>
            重试
          </Button>
        </div>
      ) : null}

      {planState.pendingApprovalId && !planState.sheetOpen ? (
        <div className={w.banner} role="status">
          <Icon name="clock" size={16} />
          <span className={w.bannerText}>{planState.pendingQueryError ?? '有一项待确认的理财操作'}</span>
          <Button
            variant="secondary"
            disabled={planState.querying}
            onClick={() => void approval.approval.actions.viewStatus()}
          >
            {planState.querying ? '查询中…' : '查看状态'}
          </Button>
        </div>
      ) : null}

      <div id="wealth-panel" role="tabpanel" aria-labelledby={`理财视图-${tab}`}>
        {tab === 'holdings' ? (
          <div className={styles.stack}>
            {assetsStatus === 'initial' || (assetsStatus === 'loading' && !overview) ? (
              <div className={styles.stack} role="status" aria-live="polite">
                <Skeleton height={18} />
                <Skeleton height={96} />
                <SkeletonText lines={4} />
                <p className={w.count}>正在加载资产…</p>
              </div>
            ) : null}

            {assetsStatus === 'error' && !overview ? (
              <StateView
                state="error"
                title="资产加载失败"
                description={assetsError ?? '请重试。'}
                actionLabel="重试"
                onAction={() => void loadAssets('retry')}
              />
            ) : null}

            {overview ? (
              <>
                <div className={w.overview} data-sb-sheen>
                  <div className={w.overviewTop}>
                    <div style={{ minWidth: 0 }}>
                      <span className={w.label}>投资总资产 · CNY</span>
                      <span className={w.bigAmount}>{hidden ? '••••••' : formatAmount(overview.totalAssetCents, false)}</span>
                    </div>
                    <div className={w.pnl}>
                      <span className={[w.pnlValue, overview.holdingPnlCents >= 0 ? w.up : w.down].join(' ')}>
                        {formatAmountSigned(overview.holdingPnlCents, hidden)}
                      </span>
                      <span className={w.pnlSub}>
                        持仓盈亏 {hidden ? '••' : formatPercent(overview.holdingPnlPercent)}
                      </span>
                    </div>
                    <IconButton
                      label={hidden ? '显示金额' : '隐藏金额'}
                      aria-pressed={hidden}
                      onClick={toggleHidden}
                    >
                      <Icon name={hidden ? 'eye-off' : 'eye'} size={18} />
                    </IconButton>
                  </div>

                  <div className={w.metrics}>
                    <div className={w.metric}>
                      <span className={w.metricLabel}>今日盈亏</span>
                      <span className={[w.metricValue, overview.todayPnlCents >= 0 ? w.up : w.down].join(' ')}>
                        {formatAmountSigned(overview.todayPnlCents, hidden)}
                      </span>
                    </div>
                    <div className={w.metric}>
                      <span className={w.metricLabel}>持仓盈亏</span>
                      <span className={[w.metricValue, overview.holdingPnlCents >= 0 ? w.up : w.down].join(' ')}>
                        {formatAmountSigned(overview.holdingPnlCents, hidden)}
                      </span>
                    </div>
                    <div className={w.metric}>
                      <span className={w.metricLabel}>可用余额</span>
                      <span className={w.metricValue}>{formatAmount(overview.availableBalanceCents, hidden)}</span>
                    </div>
                    <div className={w.metric}>
                      <span className={w.metricLabel}>持仓市值</span>
                      <span className={w.metricValue}>{formatAmount(overview.holdingValueCents, hidden)}</span>
                    </div>
                    <div className={w.metric}>
                      <span className={w.metricLabel}>在途资金</span>
                      <span className={w.metricValue}>{formatAmount(overview.inTransitCents, hidden)}</span>
                    </div>
                    <div className={w.metric}>
                      <span className={w.metricLabel}>昨日收益</span>
                      <span className={w.metricValue}>{formatAmount(overview.yesterdayIncomeCents, hidden)}</span>
                    </div>
                  </div>
                  <span className={w.updated}>数据更新时间 {overview.updatedAt}</span>
                </div>

                {overview.riskAssessment !== 'valid' ? (
                  <div className={`${w.banner} ${w.alertBanner}`} role="alert">
                    <Icon name="alert" size={16} />
                    <span className={w.bannerText}>
                      {overview.riskAssessment === 'missing'
                        ? '你还没有完成风险测评，部分产品可能无法申购。'
                        : '风险测评已过期，请重新测评后再申购。'}
                    </span>
                    <Button variant="secondary" onClick={() => setNotice('风险测评功能原型尚未开放。')}>
                      去测评
                    </Button>
                  </div>
                ) : (
                  <div className={w.banner} role="status">
                    <Icon name="shield" size={16} />
                    <span className={w.bannerText}>风险测评状态：有效期内的测评结果（演示）。</span>
                  </div>
                )}

                <div className={styles.sectionCard}>
                  <h2 className={styles.sectionLabel}>资产趋势</h2>
                  {overview.trend.length === 0 ? (
                    <p className={w.trendEmpty}>暂无趋势数据</p>
                  ) : (
                    <div className={w.trendWrap}>
                      <TrendChart points={overview.trend} hidden={hidden} />
                      <span className={w.updated}>
                        区间：{overview.trend[0]?.label} 至 {overview.trend[overview.trend.length - 1]?.label}
                      </span>
                    </div>
                  )}
                  <div className={w.periodRow} role="group" aria-label="趋势周期">
                    {PERIODS.map((p) => (
                      <button
                        key={p}
                        type="button"
                        className={[w.periodChip, p === period ? w.periodActive : ''].filter(Boolean).join(' ')}
                        aria-pressed={p === period}
                        onClick={() => changePeriod(p)}
                      >
                        {p === '1M' ? '近 1 月' : p === '3M' ? '近 3 月' : '近 1 年'}
                      </button>
                    ))}
                  </div>
                </div>

                <div className={styles.sectionCard}>
                  <h2 className={styles.sectionLabel}>快捷入口</h2>
                  <div className={w.shortcuts}>
                    {['我的订单', '定投专区', '收益明细', '风险测评'].map((label) => (
                      <button
                        key={label}
                        type="button"
                        className={w.shortcut}
                        onClick={() => setNotice(`${label}：原型尚未开放。`)}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                </div>

                <div className={styles.sectionCard}>
                  <span className={styles.sectionLabel}>持仓摘要</span>
                  {holdings.length === 0 ? (
                    <StateView
                      state="empty"
                      compact
                      title="暂无持仓"
                      description="你还没有理财持仓，可以先去发现合适的产品。"
                      actionLabel="去发现"
                      onAction={() => setTab('discover')}
                    />
                  ) : (
                    holdings.map((h) => (
                      <div className={w.row} key={h.holdingId}>
                        <span className={w.rowMain}>
                          <span className={w.rowTitle}>
                            <span className={w.rowName}>{h.name}</span>
                            <span className={[w.riskTag, riskClass(h.riskBand)].join(' ')}>{h.riskBand}</span>
                          </span>
                          <span className={w.rowMeta}>
                            净值 {formatNetValue(h.netValue)} · 份额 {formatShares(h.shares)}
                          </span>
                        </span>
                        <span className={w.rowValue}>
                          {formatAmount(h.marketValueCents, hidden)}
                          <span className={[w.rowSub, h.pnlPercent >= 0 ? w.up : w.down].join(' ')}>
                            {hidden ? '••' : formatPercent(h.pnlPercent)}
                          </span>
                        </span>
                      </div>
                    ))
                  )}
                </div>
              </>
            ) : null}
          </div>
        ) : (
          <div className={styles.stack}>
            <div className={w.catRow} role="group" aria-label="产品分类">
              {CATEGORY_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  className={[w.catChip, category === opt.value ? w.catActive : ''].filter(Boolean).join(' ')}
                  aria-pressed={category === opt.value}
                  onClick={() => setCategory(opt.value)}
                >
                  {opt.label}
                </button>
              ))}
            </div>

            <div className={styles.sectionCard}>
              <button
                type="button"
                className={[w.sectionToggle, sections.includes('products') ? w.sectionOpen : ''].filter(Boolean).join(' ')}
                aria-expanded={sections.includes('products')}
                aria-controls="discover-products"
                onClick={() => toggleSection('products')}
              >
                <span>产品列表</span>
                <Icon name="chevron-right" size={16} />
              </button>
              {sections.includes('products') ? (
                <div id="discover-products">
                  {productsStatus === 'error' ? (
                    <StateView
                      state="error"
                      compact
                      title="产品加载失败"
                      description={productsError ?? '请重试。'}
                      actionLabel="重试"
                      onAction={() => void loadProducts()}
                    />
                  ) : productsStatus !== 'loaded' ? (
                    <SkeletonText lines={4} />
                  ) : visibleProducts.length === 0 ? (
                    <StateView state="empty" compact title="没有符合条件的产品" actionLabel="查看全部" onAction={() => setCategory('all')} />
                  ) : (
                    visibleProducts.map((p) => (
                      <div className={w.row} key={p.productId}>
                        <span className={w.rowMain}>
                          <span className={w.rowTitle}>
                            <span className={w.rowName}>{p.name}</span>
                            <span className={[w.riskTag, riskClass(p.riskBand)].join(' ')}>{p.riskBand}</span>
                            {p.tags?.map((t) => (
                              <span className={w.tag} key={t}>
                                {t}
                              </span>
                            ))}
                          </span>
                          <span className={w.rowMeta}>
                            {p.metricLabel} {p.metricText} · 起购 {formatAmount(p.minPurchaseCents, hidden)} · {p.termText}
                            {!p.purchasable && p.unavailableReason ? ` · ${p.unavailableReason}` : ''}
                          </span>
                        </span>
                        <Button
                          variant="secondary"
                          aria-label={`${p.purchasable ? '申购' : '不可申购'} ${p.name}`}
                          disabled={!p.purchasable}
                          onClick={() => {
                            // 改选产品：作废旧预演/PlanView/approval 与旧重试上下文，清空金额输入
                            approval.approval.actions.invalidate();
                            approval.clearRetryContext();
                            setSelectedProductId(p.productId);
                            setAmountText('');
                            setAmountError(null);
                          }}
                        >
                          {p.purchasable ? '申购' : '不可申购'}
                        </Button>
                      </div>
                    ))
                  )}
                </div>
              ) : null}
            </div>

            <div className={styles.sectionCard}>
              <button
                type="button"
                className={[w.sectionToggle, sections.includes('newcomer') ? w.sectionOpen : ''].filter(Boolean).join(' ')}
                aria-expanded={sections.includes('newcomer')}
                aria-controls="discover-newcomer"
                onClick={() => toggleSection('newcomer')}
              >
                <span>新客活动</span>
                <Icon name="chevron-right" size={16} />
              </button>
              {sections.includes('newcomer') ? (
                <div id="discover-newcomer">
                  <p className={w.formHint}>
                    新客专享产品可在上方「产品列表」中查看；活动权益以服务端返回为准，本页不提供领取。
                  </p>
                  <Button variant="secondary" onClick={() => setNotice('活动权益：原型尚未开放。')}>
                    查看活动说明
                  </Button>
                </div>
              ) : null}
            </div>

            {selectedProduct ? (
              <div className={w.formPanel} role="group" aria-label="申购">
                <span className={w.formTitle}>
                  申购 {selectedProduct.name}（{selectedProduct.riskBand}）
                </span>
                <p className={w.formHint}>
                  {selectedProduct.metricLabel} {selectedProduct.metricText} · 起购{' '}
                  {formatAmount(selectedProduct.minPurchaseCents, hidden)} · {selectedProduct.termText}
                </p>
                <label className="sb-sr-only" htmlFor="wealth-amount">
                  申购金额（元）
                </label>
                <input
                  id="wealth-amount"
                  className={[w.input, amountError ? w.inputError : ''].filter(Boolean).join(' ')}
                  inputMode="decimal"
                  placeholder="申购金额（元）"
                  value={amountText}
                  aria-invalid={amountError ? true : undefined}
                  onChange={(e) => {
                    setAmountText(e.target.value);
                    setAmountError(null);
                    approval.approval.actions.invalidate();
                  }}
                />
                {amountError ? (
                  <p className={w.formError} role="alert">
                    <Icon name="alert" size={13} />
                    {amountError}
                  </p>
                ) : null}
                <div className={w.formActions}>
                  <Button onClick={submitPurchase}>预演申购</Button>
                  <Button
                    variant="secondary"
                    onClick={() => {
                      setSelectedProductId(null);
                      setAmountText('');
                      setAmountError(null);
                      approval.approval.actions.invalidate();
                      approval.clearRetryContext();
                    }}
                  >
                    关闭
                  </Button>
                </div>
              </div>
            ) : null}
          </div>
        )}
      </div>

      {isMock ? (
        <div className={w.demoPanel} aria-label="演示场景（仅演示）">
          <span className={styles.sectionLabel}>演示场景（仅演示）</span>
          <div className={w.demoGrid}>
            <Button
              variant="secondary"
              onClick={() => {
                applyScenario({ emptyHoldings: true });
                void loadAssets('retry');
              }}
            >
              空持仓
            </Button>
            <Button
              variant="secondary"
              onClick={() => {
                applyScenario({ emptyHoldings: false });
                void loadAssets('retry');
              }}
            >
              恢复持仓
            </Button>
            <Button
              variant="secondary"
              onClick={() => {
                applyScenario({ emptyTrend: true });
                void loadAssets('retry');
              }}
            >
              空趋势
            </Button>
            <Button
              variant="secondary"
              onClick={() => {
                applyScenario({ emptyTrend: false });
                void loadAssets('retry');
              }}
            >
              恢复趋势
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ riskAssessment: 'missing' })}>
              测评缺失
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ riskAssessment: 'expired' })}>
              测评过期
            </Button>
            <Button
              variant="secondary"
              onClick={() => {
                applyScenario({ overviewFailOnce: true });
                void loadAssets('retry');
              }}
            >
              总览失败一次
            </Button>
            <Button
              variant="secondary"
              onClick={() => {
                applyScenario({ productsFailOnce: true });
                setTab('discover');
                void loadProducts();
              }}
            >
              产品失败一次
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ offline: true })}>
              离线
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ offline: false })}>
              恢复在线
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ refreshFailOnce: true })}>
              刷新失败一次
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ otpErrorOnce: true })}>
              OTP 错误一次
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ planViewFailOnce: true })}>
              PlanView 失败一次
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ planViewExpired: true })}>
              确认前过期
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ confirm: 'failedUncertain' })}>
              明确失败
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
            <Button variant="secondary" onClick={() => applyScenario({ confirm: 'success' })}>
              恢复成功
            </Button>
          </div>
          <p className={w.demoNote}>演示场景只改变 mock 返回；接入真实服务时整块移除。</p>
        </div>
      ) : null}

      <WealthApprovalSheet approval={approval} hidden={hidden} />
    </div>
  );
}

function TrendChart({ points, hidden }: { points: Array<{ label: string; valueCents: number }>; hidden: boolean }) {
  const width = 320;
  const height = 96;
  const values = points.map((p) => p.valueCents);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const stepX = points.length > 1 ? width / (points.length - 1) : width;
  const coords = points.map((p, i) => {
    const x = i * stepX;
    const y = height - ((p.valueCents - min) / span) * (height - 12) - 6;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const summary = points
    .map((p) => `${p.label} ${hidden ? '金额已隐藏' : formatAmount(p.valueCents, false)}`)
    .join('，');
  return (
    <svg
      className={w.trendSvg}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={`资产趋势：${summary}`}
      preserveAspectRatio="none"
    >
      <polyline
        points={coords.join(' ')}
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
        style={{ color: 'var(--sb-brand)' }}
      />
    </svg>
  );
}
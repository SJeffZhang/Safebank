import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button, IconButton } from '../components/Button';
import { Icon } from '../components/Icon';
import { SearchField } from '../components/SearchField';
import { SegmentedControl } from '../components/SegmentedControl';
import { SkeletonText } from '../components/Skeleton';
import { StateView } from '../components/StateView';
import { useWealthService } from '../services/wealth/WealthService';
import { MockWealthService } from '../services/wealth/MockWealthService';
import type { MockWealthScenario } from '../services/wealth/MockWealthService';
import type { WealthHolding, WealthProduct } from '../services/wealth/types';
import { GatewayOfflineError } from '../services/approval/shared';
import { useWealthApproval, WealthApprovalSheet } from './wealth/useWealthApproval';
import {
  formatAmount,
  formatNetValue,
  formatPercent,
  formatShares,
  parseShares,
  parseYuanToCents,
} from './wealth/format';
import {
  readAmountHidden,
  readViewState,
  WEALTH_COMPARE_KEY,
  WEALTH_HOLDINGS_TAB_KEY,
  WEALTH_SEARCH_KEY,
  writeAmountHidden,
  writeViewState,
} from '../app/viewState';
import { useDocumentTitle } from '../app/hooks';
import styles from '../layout/layout.module.css';
import w from './wealth/wealth.module.css';

type LoadStatus = 'initial' | 'loading' | 'loaded' | 'empty' | 'error';
const MAX_COMPARE = 4;

function riskClass(band: string): string {
  return band === 'R1' ? w.riskR1 : band === 'R2' ? w.riskR2 : band === 'R3' ? w.riskR3 : w.riskR4;
}

export function WealthHoldingsPage() {
  const service = useWealthService();
  useDocumentTitle('全部持仓 · SafeBank');

  const [tab, setTab] = useState<string>(() => readViewState(WEALTH_HOLDINGS_TAB_KEY, 'holdings'));
  const [hidden, setHidden] = useState<boolean>(readAmountHidden);
  const [query, setQuery] = useState<string>(() => readViewState(WEALTH_SEARCH_KEY, ''));
  const [compare, setCompare] = useState<string[]>(() => readViewState<string[]>(WEALTH_COMPARE_KEY, []));
  const [notice, setNotice] = useState<string | null>(null);

  const [holdings, setHoldings] = useState<WealthHolding[]>([]);
  const [holdingsStatus, setHoldingsStatus] = useState<LoadStatus>('initial');
  const [holdingsError, setHoldingsError] = useState<string | null>(null);

  const [products, setProducts] = useState<WealthProduct[]>([]);
  const [productsStatus, setProductsStatus] = useState<LoadStatus>('initial');
  const [productsError, setProductsError] = useState<string | null>(null);

  const [offline, setOffline] = useState(false);
  const [selectedProductId, setSelectedProductId] = useState<string | null>(null);
  const [amountText, setAmountText] = useState('');
  const [amountError, setAmountError] = useState<string | null>(null);
  const [selectedHoldingId, setSelectedHoldingId] = useState<string | null>(null);
  const [sharesText, setSharesText] = useState('');
  const [sharesError, setSharesError] = useState<string | null>(null);

  const holdingsSeqRef = useRef(0);
  const productsSeqRef = useRef(0);
  const holdingsRef = useRef<WealthHolding[]>([]);

  useEffect(() => writeViewState(WEALTH_HOLDINGS_TAB_KEY, tab), [tab]);
  useEffect(() => writeViewState(WEALTH_SEARCH_KEY, query), [query]);
  useEffect(() => writeViewState(WEALTH_COMPARE_KEY, compare), [compare]);

  const toggleHidden = () => {
    setHidden((prev) => {
      writeAmountHidden(!prev);
      return !prev;
    });
  };

  const refetchAll = useCallback(async (): Promise<boolean> => {
    const seq = ++holdingsSeqRef.current;
    try {
      const [list, prods] = await Promise.all([service.getHoldings(), service.getProducts()]);
      if (seq !== holdingsSeqRef.current) return false;
      setHoldings(list);
      holdingsRef.current = list;
      setHoldingsStatus(list.length === 0 ? 'empty' : 'loaded');
      setProducts(prods);
      setProductsStatus('loaded');
      setOffline(false);
      return true;
    } catch (error) {
      if (seq !== holdingsSeqRef.current) return false;
      if (error instanceof GatewayOfflineError) setOffline(true);
      setHoldingsStatus(holdingsRef.current.length === 0 ? 'error' : 'loaded');
      setHoldingsError('持仓加载失败，请重试');
      return false;
    }
  }, [service]);

  const approval = useWealthApproval(service, refetchAll);

  const loadHoldings = useCallback(async () => {
    const seq = ++holdingsSeqRef.current;
    setHoldingsStatus((s) => (s === 'initial' ? 'initial' : 'loading'));
    setHoldingsError(null);
    try {
      const list = await service.getHoldings();
      if (seq !== holdingsSeqRef.current) return;
      setHoldings(list);
      holdingsRef.current = list;
      setHoldingsStatus(list.length === 0 ? 'empty' : 'loaded');
      setOffline(false);
    } catch (error) {
      if (seq !== holdingsSeqRef.current) return;
      if (error instanceof GatewayOfflineError) setOffline(true);
      setHoldingsError('持仓加载失败，请重试');
      setHoldingsStatus(holdingsRef.current.length === 0 ? 'error' : 'loaded');
    }
  }, [service]);

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
    void loadHoldings();
    void loadProducts();
  }, [loadHoldings, loadProducts]);

  const filteredProducts = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    if (!keyword) return products;
    return products.filter(
      (p) => p.name.toLowerCase().includes(keyword) || p.riskBand.toLowerCase().includes(keyword),
    );
  }, [products, query]);

  const selectedProducts = useMemo(
    () => compare.map((id) => products.find((p) => p.productId === id)).filter((p): p is WealthProduct => Boolean(p)),
    [compare, products],
  );

  const toggleCompare = (productId: string) => {
    setCompare((list) => {
      if (list.includes(productId)) return list.filter((id) => id !== productId);
      if (list.length >= MAX_COMPARE) {
        setNotice(`最多比较 ${MAX_COMPARE} 支产品，请先取消一个已选产品。`);
        return list;
      }
      return [...list, productId];
    });
  };

  const selectedProduct = products.find((p) => p.productId === selectedProductId) ?? null;
  const selectedHolding = holdings.find((h) => h.holdingId === selectedHoldingId) ?? null;
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
    setAmountError(null);
    void approval.startPurchase(selectedProduct.productId, parsed.cents);
  };

  const submitRedemption = () => {
    if (!selectedHolding) return;
    const parsed = parseShares(sharesText);
    if ('error' in parsed) {
      setSharesError(parsed.error);
      return;
    }
    if (parsed.shares > selectedHolding.shares + 1e-9) {
      setSharesError('超出可赎回份额');
      return;
    }
    setSharesError(null);
    void approval.startRedemption(selectedHolding.holdingId, parsed.shares);
  };

  const isMock = service instanceof MockWealthService;
  const applyScenario = (patch: Partial<MockWealthScenario>) => {
    if (isMock) (service as MockWealthService).setScenario(patch);
  };

  return (
    <div className={styles.content}>
      <div className={styles.intro}>
        <p className={styles.sectionLabel}>全部持仓</p>
        <p className={styles.introText}>完整投资组合、全部产品与产品比较。数据为演示用 mock，不代表真实收益。</p>
      </div>

      <div className={styles.row}>
        <SegmentedControl
          label="全部持仓视图"
          options={[
            { value: 'holdings', label: '持仓' },
            { value: 'products', label: '全部产品' },
            { value: 'compare', label: '比较' },
          ]}
          value={tab}
          onChange={setTab}
          panelId="holdings-panel"
        />
        <IconButton label={hidden ? '显示金额' : '隐藏金额'} aria-pressed={hidden} onClick={toggleHidden}>
          <Icon name={hidden ? 'eye-off' : 'eye'} size={18} />
        </IconButton>
      </div>

      {offline ? (
        <div className={w.banner} role="status">
          <Icon name="wifi-off" size={16} />
          <span className={w.bannerText}>当前离线，已展示内容不会丢失；恢复后重试。</span>
          <Button
            variant="secondary"
            onClick={() => {
              void loadHoldings();
              void loadProducts();
            }}
          >
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

      {planState.planStatus === 'expired' && planState.planError ? (
        <div className={`${w.banner} ${w.alertBanner}`} role="alert">
          <Icon name="alert" size={16} />
          <span className={w.bannerText}>{planState.planError}</span>
          <Button variant="secondary" onClick={approval.retryPreview}>
            重新预演
          </Button>
        </div>
      ) : null}

      {planState.planStatus === 'error' && planState.planError ? (
        <div className={`${w.banner} ${w.alertBanner}`} role="alert">
          <Icon name="alert" size={16} />
          <span className={w.bannerText}>{planState.planError}</span>
          <Button variant="secondary" onClick={approval.approval.actions.retryPlan}>
            重试预演详情
          </Button>
        </div>
      ) : null}

      {planState.planError && planState.planStatus === 'idle' ? (
        <div className={`${w.banner} ${w.alertBanner}`} role="alert">
          <Icon name="alert" size={16} />
          <span className={w.bannerText}>{planState.planError}</span>
        </div>
      ) : null}

      {approval.refreshError ? (
        <div className={w.banner} role="alert">
          <Icon name="alert" size={16} />
          <span className={w.bannerText}>{approval.refreshError}</span>
          <Button variant="secondary" onClick={() => void refetchAll()}>
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

      <div id="holdings-panel" role="tabpanel" aria-labelledby={`全部持仓视图-${tab}`}>
        {tab === 'holdings' ? (
          <div className={styles.stack}>
            {holdingsStatus === 'initial' || (holdingsStatus === 'loading' && holdings.length === 0) ? (
              <div className={styles.stack} role="status" aria-live="polite">
                <SkeletonText lines={5} />
                <p className={w.count}>正在加载持仓…</p>
              </div>
            ) : null}
            {holdingsStatus === 'error' && holdings.length === 0 ? (
              <StateView
                state="error"
                title="持仓加载失败"
                description={holdingsError ?? '请重试。'}
                actionLabel="重试"
                onAction={() => void loadHoldings()}
              />
            ) : null}
            {holdingsStatus === 'empty' ? (
              <StateView
                state="empty"
                title="暂无持仓"
                description="你还没有理财持仓，可以先去发现合适的产品。"
                actionLabel="去发现产品"
                onAction={() => setTab('products')}
              />
            ) : null}
            {holdings.length > 0 ? (
              <div className={styles.sectionCard}>
                {holdings.map((h) => (
                  <div className={w.row} key={h.holdingId}>
                    <span className={w.rowMain}>
                      <span className={w.rowTitle}>
                        <span className={w.rowName}>{h.name}</span>
                        <span className={[w.riskTag, riskClass(h.riskBand)].join(' ')}>{h.riskBand}</span>
                      </span>
                      <span className={w.rowMeta}>
                        占比 {formatPercent(h.sharePercent)} · 净值 {formatNetValue(h.netValue)} · 份额{' '}
                        {formatShares(h.shares)}
                        {!h.redeemable && h.redeemReason ? ` · ${h.redeemReason}` : ''}
                      </span>
                    </span>
                    <span className={w.rowValue}>
                      {formatAmount(h.marketValueCents, hidden)}
                      <span className={[w.rowSub, h.pnlPercent >= 0 ? w.up : w.down].join(' ')}>
                        {formatPercent(h.pnlPercent)}
                      </span>
                    </span>
                    <Button
                      variant="secondary"
                      aria-label={`${h.redeemable ? '赎回' : '不可赎回'} ${h.name}`}
                      disabled={!h.redeemable}
                      onClick={() => {
                        // 改选赎回持仓：作废旧预演/PlanView/approval 与旧重试上下文，清空份额输入
                        approval.approval.actions.invalidate();
                        approval.clearRetryContext();
                        setSelectedHoldingId(h.holdingId);
                        setSharesText('');
                        setSharesError(null);
                      }}
                    >
                      {h.redeemable ? '赎回' : '不可赎回'}
                    </Button>
                  </div>
                ))}
              </div>
            ) : null}

            {selectedHolding ? (
              <div className={w.formPanel} role="group" aria-label="赎回">
                <span className={w.formTitle}>赎回 {selectedHolding.name}</span>
                <p className={w.formHint}>
                  可赎回份额 {formatShares(selectedHolding.shares)} · 单位净值{' '}
                  {formatNetValue(selectedHolding.netValue)}
                </p>
                <label className="sb-sr-only" htmlFor="wealth-shares">
                  赎回份额
                </label>
                <input
                  id="wealth-shares"
                  className={[w.input, sharesError ? w.inputError : ''].filter(Boolean).join(' ')}
                  inputMode="decimal"
                  placeholder="赎回份额"
                  value={sharesText}
                  aria-invalid={sharesError ? true : undefined}
                  onChange={(e) => {
                    setSharesText(e.target.value);
                    setSharesError(null);
                    approval.approval.actions.invalidate();
                  }}
                />
                {sharesError ? (
                  <p className={w.formError} role="alert">
                    <Icon name="alert" size={13} />
                    {sharesError}
                  </p>
                ) : null}
                <div className={w.formActions}>
                  <Button onClick={submitRedemption}>预演赎回</Button>
                  <Button
                    variant="secondary"
                    onClick={() => {
                      setSelectedHoldingId(null);
                      setSharesText('');
                      setSharesError(null);
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
        ) : tab === 'products' ? (
          <div className={styles.stack}>
            <SearchField
              label="搜索产品或风险等级"
              value={query}
              onChange={setQuery}
              placeholder="搜索名称或 R1–R4"
            />
            <div className={w.compareBar}>
              <span>
                已选 {compare.length} / {MAX_COMPARE} 支用于比较
              </span>
              {compare.length > 0 ? (
                <Button variant="secondary" onClick={() => setCompare([])}>
                  清空选择
                </Button>
              ) : null}
            </div>

            {productsStatus === 'error' ? (
              <StateView
                state="error"
                title="产品加载失败"
                description={productsError ?? '请重试。'}
                actionLabel="重试"
                onAction={() => void loadProducts()}
              />
            ) : productsStatus !== 'loaded' ? (
              <SkeletonText lines={5} />
            ) : filteredProducts.length === 0 ? (
              <StateView
                state="empty"
                title="没有匹配的产品"
                description={`没有找到与「${query.trim()}」匹配的产品或风险等级。`}
                actionLabel="清除搜索 · 查看全部"
                onAction={() => setQuery('')}
              />
            ) : (
              <div className={styles.sectionCard}>
                {filteredProducts.map((p) => {
                  const picked = compare.includes(p.productId);
                  const atLimit = !picked && compare.length >= MAX_COMPARE;
                  return (
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
                          {p.metricLabel} {p.metricText} · 起购 {formatAmount(p.minPurchaseCents, hidden)} ·{' '}
                          {p.termText}
                          {!p.purchasable && p.unavailableReason ? ` · ${p.unavailableReason}` : ''}
                        </span>
                      </span>
                      <span className={w.rowMain} style={{ flex: 'none' }}>
                        <button
                          type="button"
                          className={[w.comparePick, picked ? w.comparePicked : ''].filter(Boolean).join(' ')}
                          aria-pressed={picked}
                          aria-label={`${picked ? '取消比较' : '加入比较'} ${p.name}`}
                          disabled={atLimit}
                          onClick={() => toggleCompare(p.productId)}
                        >
                          {picked ? '已选' : atLimit ? '已满' : '比较'}
                        </button>
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
                  );
                })}
              </div>
            )}

            {selectedProduct ? (
              <div className={w.formPanel} role="group" aria-label="申购">
                <span className={w.formTitle}>
                  申购 {selectedProduct.name}（{selectedProduct.riskBand}）
                </span>
                <p className={w.formHint}>
                  {selectedProduct.metricLabel} {selectedProduct.metricText} · 起购{' '}
                  {formatAmount(selectedProduct.minPurchaseCents, hidden)} · {selectedProduct.termText}
                </p>
                <label className="sb-sr-only" htmlFor="holdings-amount">
                  申购金额（元）
                </label>
                <input
                  id="holdings-amount"
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
        ) : (
          <div className={styles.stack}>
            {selectedProducts.length === 0 ? (
              <StateView
                state="empty"
                title="还没有选择要比较的产品"
                description={`在「全部产品」中选择最多 ${MAX_COMPARE} 支产品后即可比较。`}
                actionLabel="返回全部产品"
                onAction={() => setTab('products')}
              />
            ) : selectedProducts.length === 1 ? (
              <StateView
                state="empty"
                title="至少选择 2 支产品才能比较"
                description={`已选择 1 支产品；再选择至少 1 支即可比较。最多 ${MAX_COMPARE} 支。`}
                actionLabel="返回全部产品"
                onAction={() => setTab('products')}
              />
            ) : (
              <div className={styles.sectionCard}>
                <div className={w.compareBar}>
                  <span>
                    已选 {selectedProducts.length} / {MAX_COMPARE} 支
                  </span>
                  <Button variant="secondary" onClick={() => setTab('products')}>
                    返回全部产品
                  </Button>
                </div>
                <div style={{ overflowX: 'auto' }}>
                  <table className={w.compareTable}>
                    <caption className="sb-sr-only">
                      产品比较：风险等级、起购金额、期限与到账说明、已标注周期的历史指标
                    </caption>
                    <thead>
                      <tr>
                        <th scope="col">字段</th>
                        {selectedProducts.map((p) => (
                          <th scope="col" key={p.productId}>
                            {p.name}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      <tr>
                        <th scope="row">风险等级</th>
                        {selectedProducts.map((p) => (
                          <td key={p.productId}>
                            <span className={[w.riskTag, riskClass(p.riskBand)].join(' ')}>{p.riskBand}</span>
                          </td>
                        ))}
                      </tr>
                      <tr>
                        <th scope="row">起购金额</th>
                        {selectedProducts.map((p) => (
                          <td key={p.productId}>{formatAmount(p.minPurchaseCents, hidden)}</td>
                        ))}
                      </tr>
                      <tr>
                        <th scope="row">期限/到账</th>
                        {selectedProducts.map((p) => (
                          <td key={p.productId}>{p.termText}</td>
                        ))}
                      </tr>
                      <tr>
                        <th scope="row">历史指标</th>
                        {selectedProducts.map((p) => (
                          <td key={p.productId}>
                            {p.metricText}
                            <span className={w.rowSub}>{p.metricLabel}</span>
                          </td>
                        ))}
                      </tr>
                      <tr>
                        <th scope="row">申购状态</th>
                        {selectedProducts.map((p) => (
                          <td key={p.productId}>{p.purchasable ? '可申购' : p.unavailableReason ?? '不可申购'}</td>
                        ))}
                      </tr>
                    </tbody>
                  </table>
                </div>
                <p className={w.demoNote}>
                  以上指标口径与周期已标注，不同口径不做排名，也不构成任何投资建议。
                </p>
              </div>
            )}
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
                void loadHoldings();
              }}
            >
              空持仓
            </Button>
            <Button
              variant="secondary"
              onClick={() => {
                applyScenario({ emptyHoldings: false });
                void loadHoldings();
              }}
            >
              恢复持仓
            </Button>
            <Button
              variant="secondary"
              onClick={() => {
                applyScenario({ productsFailOnce: true });
                setTab('products');
                void loadProducts();
              }}
            >
              产品失败一次
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ overviewFailOnce: true })}>
              总览失败一次
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
            <Button variant="secondary" onClick={() => applyScenario({ planViewExpired: true })}>
              确认前过期
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
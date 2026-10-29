import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button } from '../components/Button';
import { Icon } from '../components/Icon';
import { IconButton } from '../components/Button';
import { Skeleton, SkeletonText } from '../components/Skeleton';
import { StateView } from '../components/StateView';
import { BillsOfflineError, useBillsService } from '../services/bills/BillsService';
import { MockBillsService } from '../services/bills/MockBillsService';
import type { MockBillsScenario } from '../services/bills/MockBillsService';
import type {
  AnomalyTransaction,
  BillTransaction,
  MonthlyBill,
} from '../services/bills/types';
import { formatYuan } from '../services/transfer/money';
import { useDocumentTitle } from '../app/hooks';
import { readViewState, writeViewState, BILL_CATEGORY_KEY, BILL_MONTH_KEY } from '../app/viewState';
import styles from '../layout/layout.module.css';
import bstyles from './Bills.module.css';

type LoadState = 'initial' | 'loading' | 'loaded' | 'empty' | 'error';

const CAT_COLORS = ['#111318', '#5A5D66', '#C08A34', '#B6B4AC', '#E4E4DD'];

/** 比例（0~1）格式化为百分比，例如 0.34 → "34%"。与金额格式分离，不做「分」处理。 */
function fmtPercent(share: number): string {
  return `${Math.round(share * 100)}%`;
}

function groupTransactions(list: BillTransaction[]): Map<string, BillTransaction[]> {
  const map = new Map<string, BillTransaction[]>();
  for (const t of list) {
    const arr = map.get(t.date) ?? [];
    arr.push(t);
    map.set(t.date, arr);
  }
  return map;
}

export function BillsPage() {
  const service = useBillsService();
  useDocumentTitle('账单与异常 · SafeBank');

  const [months, setMonths] = useState<string[]>([]);
  const [month, setMonth] = useState<string>(() => readViewState(BILL_MONTH_KEY, ''));
  const [category, setCategory] = useState<string | null>(() => readViewState(BILL_CATEGORY_KEY, null));
  const [bill, setBill] = useState<MonthlyBill | null>(null);
  const [loadState, setLoadState] = useState<LoadState>('initial');
  const [offline, setOffline] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [hideAmounts, setHideAmounts] = useState(false);
  const [expandedTx, setExpandedTx] = useState<string | null>(null);
  const [expandedAnomaly, setExpandedAnomaly] = useState<string | null>(null);

  const seqRef = useRef(0);
  const billRef = useRef<MonthlyBill | null>(null);
  const initSeqRef = useRef(0);
  const initInFlightRef = useRef(false);

  const loadMonth = useCallback(
    async (m: string) => {
      if (!m) return;
      const seq = ++seqRef.current;
      setLoadState('loading');
      setLoadError(null);
      setOffline(false);
      try {
        const data = await service.getMonthlyBill(m);
        if (seq !== seqRef.current) return; // 旧响应不覆盖新月
        billRef.current = data;
        setBill(data);
        setLoadState(data.transactions.length === 0 ? 'empty' : 'loaded');
      } catch (error) {
        if (seq !== seqRef.current) return;
        if (error instanceof BillsOfflineError) {
          setOffline(true);
          setLoadState(billRef.current ? 'loaded' : 'error');
        } else {
          setLoadError(`${m} 加载失败，请重试`);
          setLoadState(billRef.current ? 'loaded' : 'error');
        }
      }
    },
    [service],
  );

  /** 完整初始化：获取月份列表 → 选择有效月份 → 加载账单；可重复调用（重试/恢复在线）。 */
  const initializeBills = useCallback(async () => {
    if (initInFlightRef.current) return;
    initInFlightRef.current = true;
    const seq = ++initSeqRef.current;
    setOffline(false);
    setLoadError(null);
    try {
      const available = await service.getAvailableMonths();
      if (seq !== initSeqRef.current) return; // 旧初始化响应不覆盖
      setMonths(available);
      const saved = readViewState(BILL_MONTH_KEY, '');
      const next = available.includes(saved) ? saved : available[0];
      setMonth(next);
      if (next !== saved) writeViewState(BILL_MONTH_KEY, next); // 失效持久化月份回退并更新
      await loadMonth(next);
    } catch (error) {
      if (seq !== initSeqRef.current) return;
      if (error instanceof BillsOfflineError) setOffline(true);
      setLoadError('账单月份加载失败，请重试');
      setLoadState(billRef.current ? 'loaded' : 'error');
    } finally {
      initInFlightRef.current = false;
    }
  }, [service, loadMonth]);

  useEffect(() => {
    void initializeBills();
  }, [initializeBills]);

  const selectMonth = (m: string) => {
    setMonth(m);
    writeViewState(BILL_MONTH_KEY, m);
    void loadMonth(m);
  };

  const selectCategory = (c: string | null) => {
    setCategory(c);
    writeViewState(BILL_CATEGORY_KEY, c);
  };

  const fmt = (cents: number): string => (hideAmounts ? '••••' : formatYuan(cents));

  const filteredTransactions = useMemo(() => {
    if (!bill) return [];
    if (!category) return bill.transactions;
    return bill.transactions.filter((t) => t.category === category);
  }, [bill, category]);

  const isMock = service instanceof MockBillsService;

  const applyScenario = (patch: Partial<MockBillsScenario>) => {
    if (isMock) (service as MockBillsService).setScenario(patch);
  };

  /** 月份列表尚未加载时重试完整初始化；否则重试当前月份。 */
  const retry = () => {
    if (months.length === 0) void initializeBills();
    else void loadMonth(month);
  };

  const isLoading = loadState === 'loading' || loadState === 'initial';
  const showEmptyMonth = loadState === 'empty';

  return (
    <div className={styles.content}>
      <div className={styles.intro}>
        <p className={styles.sectionLabel}>账单与异常</p>
        <p className={styles.introText}>
          阅读账单、流水与异常证据。本页只读，异常判定与证据均来自安全网关接口。
        </p>
      </div>

      <div className={bstyles.header}>
        <select
          className={bstyles.monthSelect}
          aria-label="月份"
          value={month}
          onChange={(event) => selectMonth(event.target.value)}
        >
          {months.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
        <IconButton
          label={hideAmounts ? '显示金额' : '隐藏金额'}
          aria-pressed={hideAmounts}
          variant="secondary"
          className={bstyles.privacyToggle}
          onClick={() => setHideAmounts((v) => !v)}
        >
          <Icon name={hideAmounts ? 'eye-off' : 'eye'} size={18} />
        </IconButton>
      </div>

      {offline ? (
        <div className={bstyles.banner} role="status">
          <Icon name="wifi-off" size={16} />
          <span className={bstyles.bannerText}>当前离线，已展示内容不会丢失；恢复后重试。</span>
          <Button variant="secondary" onClick={retry}>
            重试
          </Button>
        </div>
      ) : null}

      {loadError && bill ? (
        <div className={bstyles.banner} role="alert">
          <Icon name="alert" size={16} />
          <span className={bstyles.bannerText}>{loadError}</span>
          <Button variant="secondary" onClick={retry}>
            重试
          </Button>
        </div>
      ) : null}

      {loadState === 'error' && !bill && !offline ? (
        <StateView
          state="error"
          title="账单加载失败"
          description={loadError ?? '请重试。'}
          actionLabel="重试"
          onAction={retry}
        />
      ) : null}

      {isLoading && !bill ? (
        <div className={styles.stack} role="status" aria-live="polite">
          <Skeleton height={18} />
          <Skeleton height={64} />
          <SkeletonText lines={3} />
          <p className={bstyles.count}>正在加载账单…</p>
        </div>
      ) : null}

      {isLoading && bill ? (
        <p className={bstyles.count} role="status">
          正在加载 {month} 账单…
        </p>
      ) : null}

      {showEmptyMonth && bill ? (
        <StateView
          state="empty"
          title={`${month} 暂无交易`}
          description="这个月没有任何流水，可以切换到其他月份查看。"
          actionLabel="切换到有流水的月份"
          onAction={() => {
            const other = months.find((m) => m !== month);
            if (other) selectMonth(other);
          }}
        />
      ) : null}

      {bill && !showEmptyMonth ? (
        <BillContent
          bill={bill}
          hideAmounts={hideAmounts}
          fmt={fmt}
          category={category}
          filtered={filteredTransactions}
          onCategory={selectCategory}
          expandedTx={expandedTx}
          setExpandedTx={setExpandedTx}
          expandedAnomaly={expandedAnomaly}
          setExpandedAnomaly={setExpandedAnomaly}
        />
      ) : null}

      {isMock ? (
        <div className={bstyles.demoPanel} aria-label="演示场景（仅演示）">
          <span className={styles.sectionLabel}>演示场景（仅演示）</span>
          <div className={bstyles.demoGrid}>
            <Button variant="secondary" onClick={() => applyScenario({ initialFailOnce: true, failMonth: null })}>
              初次加载失败一次
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ failMonth: '2026-08', initialFailOnce: false })}>
              08 月加载失败
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ offline: true })}>
              离线
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ offline: false })}>
              恢复在线
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ slowMonth: '2026-09', offline: false, failMonth: null, initialFailOnce: false })}>
              09 月慢响应
            </Button>
          </div>
          <p className={bstyles.demoNote}>
            演示场景只改变 mock 返回，不注入商户名/备注/账单事实；接入真实服务时整块移除。
          </p>
        </div>
      ) : null}
    </div>
  );
}

interface BillContentProps {
  bill: MonthlyBill;
  hideAmounts: boolean;
  fmt: (cents: number) => string;
  category: string | null;
  filtered: BillTransaction[];
  onCategory: (c: string | null) => void;
  expandedTx: string | null;
  setExpandedTx: (id: string | null) => void;
  expandedAnomaly: string | null;
  setExpandedAnomaly: (id: string | null) => void;
}

function BillContent(props: BillContentProps) {
  const { bill, fmt, category, filtered, onCategory, expandedTx, setExpandedTx, expandedAnomaly, setExpandedAnomaly } =
    props;

  return (
    <div className={styles.stack}>
      <SummaryAndCategories bill={bill} fmt={fmt} />
      <TrendList bill={bill} fmt={fmt} />
      <AnomalySection
        bill={bill}
        fmt={fmt}
        expandedAnomaly={expandedAnomaly}
        setExpandedAnomaly={setExpandedAnomaly}
      />
      <LedgerSection
        bill={bill}
        fmt={fmt}
        category={category}
        filtered={filtered}
        onCategory={onCategory}
        expandedTx={expandedTx}
        setExpandedTx={setExpandedTx}
      />
    </div>
  );
}

function SummaryAndCategories({ bill, fmt }: { bill: MonthlyBill; fmt: (c: number) => string }) {
  return (
    <div className={bstyles.summary}>
      <span className={bstyles.amountLabel}>总支出 · {bill.month}</span>
      <span className={bstyles.bigAmount}>{fmt(bill.totalExpenseCents)}</span>
      <div className={bstyles.metaRow}>
        <span>收入 {fmt(bill.totalIncomeCents)}</span>
        {bill.month === '2026-09' ? (
          <span className={bstyles.pill}>环比 +12.4%</span>
        ) : null}
      </div>

      {bill.categories.length > 0 ? (
        <>
          <div
            className={bstyles.catBar}
            role="img"
            aria-label={`分类占比：${bill.categories
              .map((c) => `${c.category} ${fmtPercent(c.share)}`)
              .join('，')}`}
          >
            {bill.categories.map((c, i) => (
              <i key={c.category} style={{ width: `${c.share * 100}%`, background: CAT_COLORS[i % CAT_COLORS.length] }} />
            ))}
          </div>
          <div className={bstyles.catLegend}>
            {bill.categories.map((c, i) => (
              <span key={c.category}>
                <span className={bstyles.legendDot} style={{ background: CAT_COLORS[i % CAT_COLORS.length] }} aria-hidden="true" />
                {c.category} {fmtPercent(c.share)}
              </span>
            ))}
          </div>
        </>
      ) : (
        <p className={bstyles.count}>本月无分类汇总。</p>
      )}
    </div>
  );
}

function TrendList({ bill, fmt }: { bill: MonthlyBill; fmt: (c: number) => string }) {
  if (bill.dailyTrend.length === 0) {
    return <p className={bstyles.count}>本月无日趋势数据。</p>;
  }
  const max = Math.max(...bill.dailyTrend.map((d) => d.amountCents), 1);
  return (
    <div className={styles.sectionCard}>
      <h3 className={bstyles.sectionTitle}>日趋势</h3>
      <div className={bstyles.trendRow}>
        {bill.dailyTrend.map((d) => (
          <div className={bstyles.trendCol} key={d.date}>
            <span className="sb-sr-only">{`${d.date} ${fmt(d.amountCents)}`}</span>
            <div
              className={bstyles.trendBar}
              style={{ height: `${Math.max(4, Math.round((d.amountCents / max) * 100))}%` }}
              aria-hidden="true"
            />
            <span className={bstyles.trendDate}>{d.date}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function AnomalySection({
  bill,
  fmt,
  expandedAnomaly,
  setExpandedAnomaly,
}: {
  bill: MonthlyBill;
  fmt: (c: number) => string;
  expandedAnomaly: string | null;
  setExpandedAnomaly: (id: string | null) => void;
}) {
  return (
    <div className={styles.sectionCard}>
      <h3 className={bstyles.sectionTitle}>
        <span>异常交易</span>
        {bill.anomalies.length > 0 ? <span className={bstyles.pill}>{bill.anomalies.length} 笔</span> : null}
      </h3>
      {bill.anomalies.length === 0 ? (
        <p className={bstyles.count}>本月暂未命中异常规则，不代表绝对安全。</p>
      ) : (
        <div className={bstyles.anomalyList} data-testid="anomaly-list">
          {bill.anomalies.map((a) => (
            <AnomalyCard
              key={a.id}
              anomaly={a}
              fmt={fmt}
              open={expandedAnomaly === a.id}
              onToggle={() => setExpandedAnomaly(expandedAnomaly === a.id ? null : a.id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function AnomalyCard({
  anomaly,
  fmt,
  open,
  onToggle,
}: {
  anomaly: AnomalyTransaction;
  fmt: (c: number) => string;
  open: boolean;
  onToggle: () => void;
}) {
  const bodyId = `anomaly-evidence-${anomaly.id}`;
  return (
    <div className={bstyles.anomaly}>
      <button
        type="button"
        className={`${bstyles.anomalyHead} ${open ? bstyles.open : ''}`}
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={onToggle}
      >
        <span className={bstyles.anomalyHeadMain}>
          <span className={bstyles.anomalyTitle}>{anomaly.merchant}</span>
          <span className={bstyles.anomalySub}>
            {anomaly.time} · {anomaly.summary} · 命中 {anomaly.rulesCount} 条规则
            {anomaly.status === 'pending' ? ' · 待入账' : ''}
          </span>
        </span>
        <span className={bstyles.anomalyAmount}>{fmt(anomaly.amountCents)}</span>
        <Icon name="chevron-right" size={16} />
      </button>
      {open ? (
        <div className={bstyles.evidence} id={bodyId}>
          {anomaly.evidence.map((e) => (
            <div className={bstyles.evidenceRow} key={e.label}>
              <Icon name="shield" size={13} />
              <span>
                <span className={bstyles.evidenceLabel}>{e.label}：</span>
                {e.description}
              </span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function LedgerSection({
  bill,
  fmt,
  category,
  filtered,
  onCategory,
  expandedTx,
  setExpandedTx,
}: {
  bill: MonthlyBill;
  fmt: (c: number) => string;
  category: string | null;
  filtered: BillTransaction[];
  onCategory: (c: string | null) => void;
  expandedTx: string | null;
  setExpandedTx: (id: string | null) => void;
}) {
  const groups = groupTransactions(filtered);
  const dates = Array.from(groups.keys());

  return (
    <div className={styles.sectionCard}>
      <h3 className={bstyles.sectionTitle}>流水</h3>

      <div className={bstyles.chips}>
        <button
          type="button"
          className={`${bstyles.chip} ${category === null ? bstyles.chipActive : ''}`}
          aria-pressed={category === null}
          onClick={() => onCategory(null)}
        >
          全部
        </button>
        {bill.categories.map((c) => (
          <button
            type="button"
            key={c.category}
            className={`${bstyles.chip} ${category === c.category ? bstyles.chipActive : ''}`}
            aria-pressed={category === c.category}
            onClick={() => onCategory(category === c.category ? null : c.category)}
          >
            {c.category}
          </button>
        ))}
      </div>

      <p className={bstyles.count}>
        显示 {filtered.length} 笔 / 共 {bill.transactions.length} 笔
      </p>

      {filtered.length === 0 ? (
        <StateView
          state="empty"
          compact
          title="没有符合筛选的流水"
          description="当前类别无流水，清除筛选可查看全部。"
          actionLabel="清除筛选"
          onAction={() => onCategory(null)}
        />
      ) : (
        dates.map((date) => (
          <div key={date}>
            <p className={bstyles.count}>{date}</p>
            {groups.get(date)!.map((t) => (
              <TxRow
                key={t.id}
                tx={t}
                fmt={fmt}
                open={expandedTx === t.id}
                onToggle={() => setExpandedTx(expandedTx === t.id ? null : t.id)}
              />
            ))}
          </div>
        ))
      )}
    </div>
  );
}

function TxRow({
  tx,
  fmt,
  open,
  onToggle,
}: {
  tx: BillTransaction;
  fmt: (c: number) => string;
  open: boolean;
  onToggle: () => void;
}) {
  const detailId = `tx-detail-${tx.id}`;
  const row = (
    <div className={bstyles.ledger} onClick={onToggle} role="button" tabIndex={0} aria-expanded={open} aria-controls={open ? detailId : undefined} onKeyDown={(e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        onToggle();
      }
    }}>
      <span className={bstyles.ledgerMain}>
        <span className={bstyles.ledgerTitle}>{tx.merchant}</span>
        <span className={bstyles.ledgerMeta}>
          {tx.category}
          {tx.status === 'pending' ? ' · 待入账' : ''}
          {tx.memo ? ` · ${tx.memo}` : ''}
        </span>
      </span>
      <span
        className={`${bstyles.ledgerAmount} ${
          tx.kind === 'income' || tx.kind === 'refund' ? bstyles.income : bstyles.expense
        }`}
        data-trend={tx.kind === 'income' || tx.kind === 'refund' ? 'income' : 'expense'}
      >
        {tx.amountCents < 0 ? `-${fmt(Math.abs(tx.amountCents))}` : `+${fmt(tx.amountCents)}`}
      </span>
    </div>
  );

  return (
    <div>
      {row}
      {open ? (
        <div className={bstyles.txDetail} id={detailId}>
          <span>
            商户：<b>{tx.merchant}</b>
          </span>
          <span>
            备注：<b>{tx.memo || '无备注'}</b>
          </span>
          <span>分类：{tx.category}</span>
          <span>
            金额：{fmt(Math.abs(tx.amountCents))} · {tx.status === 'pending' ? '待入账' : '已入账'}
          </span>
        </div>
      ) : null}
    </div>
  );
}
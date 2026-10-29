import type {
  AnomalyTransaction,
  BillTransaction,
  CategoryTotal,
  DailyTrend,
  MonthlyBill,
} from './types';
import { BillsOfflineError } from './BillsService';
import type { BillsService } from './BillsService';

export interface MockBillsScenario {
  /** 首次 getMonthlyBill 失败一次（模拟初次加载失败后重试）。 */
  initialFailOnce: boolean;
  /** 指定在该月份加载时失败（新月失败保留旧月）。 */
  failMonth: string | null;
  /** 模拟断网。 */
  offline: boolean;
  /** 慢响应月份（用于验证快速切换时旧响应被丢弃）。 */
  slowMonth: string | null;
}

export const DEFAULT_BILLS_SCENARIO: MockBillsScenario = {
  initialFailOnce: false,
  failMonth: null,
  offline: false,
  slowMonth: null,
};

const MONTHS = ['2026-09', '2026-08', '2026-07'];

function tx(
  id: string,
  date: string,
  merchant: string,
  memo: string,
  category: string,
  amountCents: number,
  status: 'posted' | 'pending' = 'posted',
  kind: 'expense' | 'income' | 'refund' = amountCents < 0 ? 'expense' : 'income',
): BillTransaction {
  return { id, date, merchant, memo, category, amountCents, status, kind };
}

// 演示数据（明确为 mock，不描述为真实账户数据）。商户/备注含 HTML 外观文本，仅验证纯文本渲染。
const SEPTEMBER_TRANSACTIONS: BillTransaction[] = [
  tx('t1', '2026-09-27', '全家便利店', '日常', '购物', -3250),
  tx('t2', '2026-09-22', '星耀电子', '新商户大额', '购物', -349900),
  tx('t3', '2026-09-18', '地铁通勤', '本月通勤', '交通', -12500),
  tx('t4', '2026-09-15', '美味外卖', '4 分钟内第 2 笔', '餐饮', -26800),
  tx('t5', '2026-09-15', '美味外卖', '连续交易', '餐饮', -26800),
  tx('t6', '2026-09-07', '夜色酒吧', '凌晨消费', '餐饮', -128000),
  tx('t7', '2026-09-05', '家庭水电', '', '生活', -8800),
  tx('t8', '2026-09-03', '<script>alert(1)</script>', '<img src=x onerror=alert(2)>', '其他', -5990),
  tx('t9', '2026-09-01', '工资入账', '9 月工资', '收入', 1200000, 'posted', 'income'),
  tx('t10', '2026-09-28', '退款中心', '购物退款', '退款', 120000, 'pending', 'refund'),
];

const SEPTEMBER_ANOMALIES: AnomalyTransaction[] = [
  {
    id: 'a1',
    time: '09-07 02:13',
    merchant: '夜色酒吧',
    amountCents: 128000,
    status: 'posted',
    summary: '凌晨大额消费',
    rulesCount: 2,
    evidence: [
      { label: '凌晨大额', description: '02:13 发生，金额超过 1,000 元，命中「凌晨 2–6 点大额」规则。' },
      { label: '单笔集中', description: '该时段仅此一笔，命中「单笔集中消费」规则。' },
    ],
  },
  {
    id: 'a2',
    time: '09-15 12:41',
    merchant: '美味外卖',
    amountCents: 26800,
    status: 'posted',
    summary: '4 分钟内第 2 笔连续交易',
    rulesCount: 1,
    evidence: [
      { label: '连续交易', description: '与上一笔间隔 4 分钟，命中「短时间连续交易」规则。' },
    ],
  },
  {
    id: 'a3',
    time: '09-22 19:05',
    merchant: '星耀电子',
    amountCents: 349900,
    status: 'posted',
    summary: '新商户大额',
    rulesCount: 2,
    evidence: [
      { label: '新商户', description: '该商户本月首次出现，命中「新商户」规则。' },
      { label: '大额', description: '金额超过 3,000 元，命中「大额交易」规则。' },
    ],
  },
];

const AUGUST_TRANSACTIONS: BillTransaction[] = [
  tx('a1', '2026-08-20', '山野咖啡', '', '餐饮', -680),
  tx('a2', '2026-08-12', 'Studio North', '项目款', '收入', 185000, 'posted', 'income'),
  tx('a3', '2026-08-05', '线上书店', '', '购物', -4200),
];

/**
 * 由流水推导月度汇总，确保总支出/分类金额/占比/日趋势完全一致。
 * - 总支出 = 已入账 expense 流水绝对值之和（待入账不计入）。
 * - 收入汇总 = 已入账 income；待入账退款不计入已到账。
 * - 空月份不除零。
 */
function deriveSummary(month: string, transactions: BillTransaction[], anomalies: AnomalyTransaction[]): MonthlyBill {
  let totalExpense = 0;
  let totalIncome = 0;
  const catMap = new Map<string, number>();
  const dateMap = new Map<string, number>();

  for (const t of transactions) {
    if (t.status === 'posted' && t.kind === 'expense') {
      const abs = Math.abs(t.amountCents);
      totalExpense += abs;
      catMap.set(t.category, (catMap.get(t.category) ?? 0) + abs);
      dateMap.set(t.date, (dateMap.get(t.date) ?? 0) + abs);
    } else if (t.status === 'posted' && t.kind === 'income') {
      totalIncome += t.amountCents;
    }
  }

  const categories: CategoryTotal[] = Array.from(catMap.entries()).map(([category, amountCents]) => ({
    category,
    amountCents,
    share: totalExpense > 0 ? amountCents / totalExpense : 0,
  }));

  const dailyTrend: DailyTrend[] = Array.from(dateMap.entries())
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([date, amountCents]) => ({ date, amountCents }));

  return {
    month,
    totalExpenseCents: totalExpense,
    totalIncomeCents: totalIncome,
    categories,
    dailyTrend,
    transactions,
    anomalies,
  };
}

const BILLS: Record<string, MonthlyBill> = {
  '2026-09': deriveSummary('2026-09', SEPTEMBER_TRANSACTIONS, SEPTEMBER_ANOMALIES),
  '2026-08': deriveSummary('2026-08', AUGUST_TRANSACTIONS, []),
  '2026-07': deriveSummary('2026-07', [], []),
};

/**
 * 演示用账单服务（内存实现，明确标注为 mock，不描述为真实账单）。
 * 延迟用 delayMs；测试可置 0；scenario 控制失败/离线/慢响应。
 */
export class MockBillsService implements BillsService {
  private scenario: MockBillsScenario;
  private delayMs: number;
  private initialFailedOnce = false;

  constructor(scenario?: Partial<MockBillsScenario>, delayMs = 300) {
    this.scenario = { ...DEFAULT_BILLS_SCENARIO, ...scenario };
    this.delayMs = delayMs;
  }

  getScenario(): MockBillsScenario {
    return { ...this.scenario };
  }

  setScenario(patch: Partial<MockBillsScenario>): void {
    this.scenario = { ...this.scenario, ...patch };
  }

  private sleep(ms = this.delayMs): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  private assertOnline(): void {
    if (this.scenario.offline) throw new BillsOfflineError();
  }

  async getAvailableMonths(): Promise<string[]> {
    this.assertOnline();
    await this.sleep();
    return [...MONTHS];
  }

  async getMonthlyBill(month: string): Promise<MonthlyBill> {
    this.assertOnline();
    if (month === this.scenario.slowMonth) await this.sleep(1200);
    else await this.sleep();

    if (this.scenario.initialFailOnce && !this.initialFailedOnce) {
      this.initialFailedOnce = true;
      throw new Error('模拟加载失败');
    }
    if (this.scenario.failMonth === month) {
      throw new Error(`模拟 ${month} 加载失败`);
    }

    const bill = BILLS[month];
    if (!bill) throw new Error(`未知月份 ${month}`);
    return structuredCloneSafe(bill);
  }
}

function structuredCloneSafe<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

/** 演示场景控制区的月份标签说明。 */
export const DEMO_MONTH_LABELS: Record<string, string> = {
  '2026-09': '有流水·有异常',
  '2026-08': '有流水·无异常',
  '2026-07': '空月份',
};
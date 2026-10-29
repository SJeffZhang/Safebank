import type { ApprovalAuthorization, ApprovalStatus, ConfirmResult } from '../transfer/types';
import type {
  RiskAssessmentStatus,
  RiskBand,
  TrendPeriod,
  TrendPoint,
  WealthHolding,
  WealthOperation,
  WealthOverview,
  WealthPlanView,
  WealthPreviewResult,
  WealthProduct,
} from './types';
import {
  ApprovalError,
  GatewayOfflineError,
  clearRecords,
  maxSeqFromIds,
  persistRecords,
  type PersistedRecord,
} from '../approval/shared';
import type { WealthService } from './WealthService';

export type MockWealthConfirmOutcome = 'success' | 'failedUncertain' | 'failedNoChange' | 'unknown';
export type MockWealthStatusOutcome = 'success' | 'failed' | 'unknown';

export interface MockWealthScenario {
  overviewFailOnce: boolean;
  productsFailOnce: boolean;
  offline: boolean;
  emptyHoldings: boolean;
  emptyTrend: boolean;
  riskAssessment: RiskAssessmentStatus;
  confirm: MockWealthConfirmOutcome;
  afterUnknownStatus: MockWealthStatusOutcome;
  planViewFailOnce: boolean;
  planViewExpired: boolean;
  /** 红级首次确认返回「验证码不正确，还可尝试 2 次」。 */
  otpErrorOnce: boolean;
  /** 下一次资产刷新失败一次（用于「操作成功但刷新失败」）。 */
  refreshFailOnce: boolean;
}

export const DEFAULT_WEALTH_SCENARIO: MockWealthScenario = {
  overviewFailOnce: false,
  productsFailOnce: false,
  offline: false,
  emptyHoldings: false,
  emptyTrend: false,
  riskAssessment: 'valid',
  confirm: 'success',
  afterUnknownStatus: 'unknown',
  planViewFailOnce: false,
  planViewExpired: false,
  otpErrorOnce: false,
  refreshFailOnce: false,
};

const RECORDS_KEY = 'mock-wealth-records';
const STATE_KEY = 'mock-wealth-state';
const ID_PREFIX = 'mock-wealth-apv-';

export const AVAILABLE_BALANCE_CENTS = 860_000; // ¥8,600.00 可用余额（申购资金来源）
export const IN_TRANSIT_CENTS = 0;
const UPDATED_AT = '2026-10-27 09:41';

interface BaseHolding extends WealthHolding {}

/** 基础持仓；市值合计 ¥128,560.30，盈亏合计 +¥236.18，与总览保持一致。 */
const BASE_HOLDINGS: BaseHolding[] = [
  {
    holdingId: 'h1',
    productId: 'p1',
    name: '稳健增利',
    riskBand: 'R3',
    marketValueCents: 5_264_000,
    pnlCents: 12_000,
    pnlPercent: 0.00229,
    sharePercent: 0,
    netValue: 1.1284,
    shares: 4_664.02,
    redeemable: true,
  },
  {
    holdingId: 'h2',
    productId: 'p2',
    name: '现金管理',
    riskBand: 'R1',
    marketValueCents: 3_018_000,
    pnlCents: 1_800,
    pnlPercent: 0.0006,
    sharePercent: 0,
    netValue: 1.0,
    shares: 3_018,
    redeemable: true,
  },
  {
    holdingId: 'h3',
    productId: 'p3',
    name: '优选债基',
    riskBand: 'R2',
    marketValueCents: 2_145_000,
    pnlCents: -5_400,
    pnlPercent: -0.0025,
    sharePercent: 0,
    netValue: 1.0462,
    shares: 2_050.28,
    redeemable: true,
  },
  {
    holdingId: 'h4',
    productId: 'p4',
    name: '中证红利',
    riskBand: 'R3',
    marketValueCents: 1_624_030,
    pnlCents: 10_218,
    pnlPercent: 0.00633,
    sharePercent: 0,
    netValue: 1.2846,
    shares: 1_264.2,
    redeemable: false,
    redeemReason: '该产品处于封闭期，暂不可赎回',
  },
  {
    holdingId: 'h5',
    productId: 'p5',
    name: '黄金积存',
    riskBand: 'R4',
    marketValueCents: 805_000,
    pnlCents: 5_000,
    pnlPercent: 0.00625,
    sharePercent: 0,
    netValue: 612.4,
    shares: 1_314.48,
    redeemable: true,
  },
];

const BASE_PRODUCTS: WealthProduct[] = [
  {
    productId: 'p1',
    name: '稳健增利',
    category: 'steady',
    riskBand: 'R3',
    minPurchaseCents: 100_000,
    termText: '赎回 T+1 到账',
    onSale: true,
    purchasable: true,
    metricLabel: '近一年（已发生）',
    metricText: '+2.46%',
    netValue: 1.1284,
    tags: ['热销'],
  },
  {
    productId: 'p2',
    name: '现金管理',
    category: 'current',
    riskBand: 'R1',
    minPurchaseCents: 100,
    termText: '随时赎回，T+0 到账',
    onSale: true,
    purchasable: true,
    metricLabel: '七日年化（历史）',
    metricText: '+0.82%',
    netValue: 1.0,
    tags: ['新客'],
  },
  {
    productId: 'p3',
    name: '优选债基',
    category: 'steady',
    riskBand: 'R2',
    minPurchaseCents: 10_000,
    termText: '赎回 T+2 到账',
    onSale: true,
    purchasable: true,
    metricLabel: '近一年（已发生）',
    metricText: '-1.82%',
    netValue: 1.0462,
  },
  {
    productId: 'p4',
    name: '中证红利',
    category: 'fund',
    riskBand: 'R3',
    minPurchaseCents: 1_000,
    termText: '赎回 T+1 到账',
    onSale: true,
    purchasable: true,
    metricLabel: '近一年（已发生）',
    metricText: '+1.34%',
    netValue: 1.2846,
  },
  {
    productId: 'p5',
    name: '黄金积存',
    category: 'gold',
    riskBand: 'R4',
    minPurchaseCents: 50_000,
    termText: '赎回 T+1 到账',
    onSale: true,
    purchasable: true,
    metricLabel: '元/克（当前）',
    metricText: '612.40',
    netValue: 612.4,
  },
  {
    productId: 'p6',
    name: '短债稳享',
    category: 'steady',
    riskBand: 'R2',
    minPurchaseCents: 100_000,
    termText: '赎回 T+1 到账',
    onSale: true,
    purchasable: false,
    unavailableReason: '本期额度已售罄，暂不可申购',
    metricLabel: '近半年（已发生）',
    metricText: '+1.12%',
    netValue: 1.021,
  },
  {
    productId: 'p7',
    name: '指数增强',
    category: 'fund',
    riskBand: 'R4',
    minPurchaseCents: 1_000,
    termText: '赎回 T+2 到账',
    onSale: true,
    purchasable: true,
    metricLabel: '近一年（已发生）',
    metricText: '+3.05%',
    netValue: 1.3302,
  },
  {
    productId: 'p8',
    name: '天天盈',
    category: 'current',
    riskBand: 'R1',
    minPurchaseCents: 100,
    termText: '随时赎回，T+0 到账',
    onSale: false,
    purchasable: false,
    unavailableReason: '该产品暂未开放申购',
    metricLabel: '七日年化（历史）',
    metricText: '+0.65%',
    netValue: 1.0,
  },
];

const TRENDS: Record<TrendPeriod, TrendPoint[]> = {
  '1M': [
    { label: '10-01', valueCents: 12_742_000 },
    { label: '10-05', valueCents: 12_788_000 },
    { label: '10-09', valueCents: 12_760_200 },
    { label: '10-13', valueCents: 12_801_400 },
    { label: '10-17', valueCents: 12_822_600 },
    { label: '10-21', valueCents: 12_839_900 },
    { label: '10-27', valueCents: 12_856_030 },
  ],
  '3M': [
    { label: '08-01', valueCents: 12_512_000 },
    { label: '08-20', valueCents: 12_598_400 },
    { label: '09-08', valueCents: 12_640_100 },
    { label: '09-26', valueCents: 12_702_500 },
    { label: '10-08', valueCents: 12_748_000 },
    { label: '10-18', valueCents: 12_810_600 },
    { label: '10-27', valueCents: 12_856_030 },
  ],
  '1Y': [
    { label: '2025-11', valueCents: 11_980_000 },
    { label: '2026-01', valueCents: 12_140_000 },
    { label: '2026-03', valueCents: 12_286_000 },
    { label: '2026-05', valueCents: 12_402_000 },
    { label: '2026-07', valueCents: 12_588_000 },
    { label: '2026-09', valueCents: 12_724_000 },
    { label: '2026-10', valueCents: 12_856_030 },
  ],
};

type AppliedOp =
  | { kind: 'purchase'; productId: string; amountCents: number }
  | { kind: 'redemption'; holdingId: string; shares: number; estimatedAmountCents: number };

/** 可用余额随已应用的申购/赎回变化，保证资金守恒（不凭空增加总资产）。 */
function computeAvailable(applied: AppliedOp[]): number {
  let available = AVAILABLE_BALANCE_CENTS;
  for (const op of applied) {
    if (op.kind === 'purchase') available -= op.amountCents;
    else available += op.estimatedAmountCents;
  }
  return available;
}

interface WealthRecord extends PersistedRecord {
  operation: WealthOperation;
  productId: string;
  holdingId?: string;
  amountCents?: number;
  shares?: number;
  /** 预演时由 service 权威规则确定的所需授权等级；确认时不得由调用方声明覆盖。 */
  requiredRisk?: 'red' | 'yellow';
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

function loadApplied(): AppliedOp[] {
  try {
    const raw = window.sessionStorage.getItem(STATE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as AppliedOp[]) : [];
  } catch {
    return [];
  }
}

function persistApplied(ops: AppliedOp[]): void {
  try {
    window.sessionStorage.setItem(STATE_KEY, JSON.stringify(ops));
  } catch {
    /* ignore */
  }
}

function normalizeRecord(rec: Partial<WealthRecord>): WealthRecord {
  return {
    id: rec.id ?? '',
    expiresAtMillis: rec.expiresAtMillis ?? 0,
    submitted: rec.submitted ?? false,
    processed: rec.processed ?? false,
    finalStatus: rec.finalStatus ?? null,
    finalReason: rec.finalReason,
    noFundsMoved: rec.noFundsMoved,
    afterUnknownStatus: rec.afterUnknownStatus ?? null,
    operation: rec.operation ?? 'purchase',
    productId: rec.productId ?? '',
    holdingId: rec.holdingId,
    amountCents: rec.amountCents,
    shares: rec.shares,
    requiredRisk: rec.requiredRisk,
  };
}

function loadWealthRecords(): WealthRecord[] {
  try {
    const raw = window.sessionStorage.getItem(RECORDS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.map((r) => normalizeRecord(r as Partial<WealthRecord>)) : [];
  } catch {
    return [];
  }
}

/** 退出登录时清除理财 mock 记录与已应用操作。 */
export function clearMockWealthRecords(): void {
  clearRecords(RECORDS_KEY);
  try {
    window.sessionStorage.removeItem(STATE_KEY);
  } catch {
    /* ignore */
  }
}

/** 由基础持仓 + 已应用操作推导持仓，保证总览与持仓一致。 */
function computeHoldings(applied: AppliedOp[], empty: boolean): WealthHolding[] {
  if (empty) return [];
  let list: WealthHolding[] = BASE_HOLDINGS.map((h) => ({ ...h }));
  for (const op of applied) {
    if (op.kind === 'purchase') {
      const product = BASE_PRODUCTS.find((p) => p.productId === op.productId);
      if (!product) continue;
      const shares = round2(op.amountCents / 100 / product.netValue);
      const existing = list.find((h) => h.productId === op.productId);
      if (existing) {
        existing.marketValueCents += op.amountCents; // 整数分相加
        existing.shares = round2(existing.shares + shares);
      } else {
        list.push({
          holdingId: `h-${op.productId}`,
          productId: op.productId,
          name: product.name,
          riskBand: product.riskBand,
          marketValueCents: op.amountCents,
          pnlCents: 0,
          pnlPercent: 0,
          sharePercent: 0,
          netValue: product.netValue,
          shares,
          redeemable: true,
        });
      }
    } else {
      const holding = list.find((h) => h.holdingId === op.holdingId);
      if (!holding) continue;
      const remaining = round2(holding.shares - op.shares);
      if (remaining <= 0) {
        list = list.filter((h) => h.holdingId !== op.holdingId);
      } else {
        holding.shares = remaining;
        holding.marketValueCents = Math.round(remaining * holding.netValue * 100);
      }
    }
  }
  const total = list.reduce((a, h) => a + h.marketValueCents, 0);
  for (const h of list) h.sharePercent = total > 0 ? h.marketValueCents / total : 0;
  return list;
}

function purchaseRisk(product: WealthProduct, amountCents: number): 'red' | 'yellow' {
  if (product.riskBand === 'R3' || product.riskBand === 'R4') return 'red';
  if (amountCents >= 500_000) return 'red';
  return 'yellow';
}

function redemptionRisk(holding: WealthHolding): 'red' | 'yellow' {
  return holding.riskBand === 'R3' || holding.riskBand === 'R4' ? 'red' : 'yellow';
}

export class MockWealthService implements WealthService {
  private scenario: MockWealthScenario;
  private delayMs: number;
  private now: () => number;
  private seq = 0;
  private planViewFailedOnce = false;
  private overviewFailedOnce = false;
  private productsFailedOnce = false;
  private refreshFailedOnce = false;
  private otpErrorUsed = false;
  private records = new Map<string, WealthRecord>();

  constructor(scenario?: Partial<MockWealthScenario>, delayMs = 300, now: () => number = () => Date.now()) {
    this.scenario = { ...DEFAULT_WEALTH_SCENARIO, ...scenario };
    this.delayMs = delayMs;
    this.now = now;
    const recs = loadWealthRecords();
    this.seq = maxSeqFromIds(recs, ID_PREFIX);
    for (const r of recs) this.records.set(r.id, r);
  }

  getScenario(): MockWealthScenario {
    return { ...this.scenario };
  }

  setScenario(patch: Partial<MockWealthScenario>): void {
    this.scenario = { ...this.scenario, ...patch };
  }

  private sleep(ms = this.delayMs): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  private assertOnline(): void {
    if (this.scenario.offline) throw new GatewayOfflineError();
  }

  private sync(): void {
    persistRecords(RECORDS_KEY, Array.from(this.records.values()));
  }

  private markRefreshFailure(): void {
    if (this.scenario.refreshFailOnce && !this.refreshFailedOnce) {
      this.refreshFailedOnce = true;
      throw new Error('模拟资产刷新失败');
    }
  }

  async getHoldings(): Promise<WealthHolding[]> {
    this.assertOnline();
    await this.sleep();
    this.markRefreshFailure();
    return computeHoldings(loadApplied(), this.scenario.emptyHoldings);
  }

  async getOverview(period: TrendPeriod): Promise<WealthOverview> {
    this.assertOnline();
    await this.sleep();
    this.markRefreshFailure();
    if (this.scenario.overviewFailOnce && !this.overviewFailedOnce) {
      this.overviewFailedOnce = true;
      throw new Error('模拟总览加载失败');
    }
    const holdings = computeHoldings(loadApplied(), this.scenario.emptyHoldings);
    const holdingValueCents = holdings.reduce((a, h) => a + h.marketValueCents, 0);
    const holdingPnlCents = holdings.reduce((a, h) => a + h.pnlCents, 0);
    const cost = holdingValueCents - holdingPnlCents;
    const availableBalanceCents = computeAvailable(loadApplied());
    return {
      // 总资产 = 持仓市值 + 可用余额 + 在途资金（三项口径一致，互不矛盾）
      totalAssetCents: holdingValueCents + availableBalanceCents + IN_TRANSIT_CENTS,
      todayPnlCents: 2_360,
      holdingPnlCents,
      holdingPnlPercent: cost > 0 ? holdingPnlCents / cost : 0,
      availableBalanceCents,
      holdingValueCents,
      inTransitCents: IN_TRANSIT_CENTS,
      yesterdayIncomeCents: 1_860,
      trend: this.scenario.emptyTrend || holdings.length === 0 ? [] : TRENDS[period],
      trendPeriod: period,
      riskAssessment: this.scenario.riskAssessment,
      updatedAt: UPDATED_AT,
      holdingsCount: holdings.length,
    };
  }

  async getProducts(): Promise<WealthProduct[]> {
    this.assertOnline();
    await this.sleep();
    if (this.scenario.productsFailOnce && !this.productsFailedOnce) {
      this.productsFailedOnce = true;
      throw new Error('模拟产品加载失败');
    }
    return BASE_PRODUCTS.map((p) => ({ ...p }));
  }

  async preparePurchase(productId: string, amountCents: number): Promise<WealthPreviewResult> {
    this.assertOnline();
    await this.sleep();
    const product = BASE_PRODUCTS.find((p) => p.productId === productId);
    if (!product) return { kind: 'error', code: 'NO_PRODUCT', message: '产品不存在' };
    if (!product.onSale) return { kind: 'error', code: 'NOT_ON_SALE', message: '该产品暂未开放' };
    if (!product.purchasable) {
      return { kind: 'error', code: 'NOT_PURCHASABLE', message: product.unavailableReason ?? '该产品暂不可申购' };
    }
    if (!Number.isInteger(amountCents) || amountCents <= 0) {
      return { kind: 'error', code: 'INVALID_AMOUNT', message: '请输入有效的申购金额' };
    }
    if (amountCents < product.minPurchaseCents) {
      return {
        kind: 'error',
        code: 'BELOW_MIN',
        message: `低于起购金额（${(product.minPurchaseCents / 100).toFixed(2)} 元）`,
      };
    }
    if (amountCents > computeAvailable(loadApplied())) {
      return { kind: 'error', code: 'INSUFFICIENT_BALANCE', message: '可用余额不足' };
    }
    return this.makeRecord({
      kind: 'purchase',
      productId,
      amountCents,
      requiredRisk: purchaseRisk(product, amountCents),
    });
  }

  async prepareRedemption(holdingId: string, shares: number): Promise<WealthPreviewResult> {
    this.assertOnline();
    await this.sleep();
    const holdings = computeHoldings(loadApplied(), this.scenario.emptyHoldings);
    const holding = holdings.find((h) => h.holdingId === holdingId);
    if (!holding) return { kind: 'error', code: 'NO_HOLDING', message: '持仓不存在' };
    if (!holding.redeemable) {
      return { kind: 'error', code: 'NOT_REDEEMABLE', message: holding.redeemReason ?? '该持仓暂不可赎回' };
    }
    if (!Number.isFinite(shares) || shares <= 0) {
      return { kind: 'error', code: 'INVALID_SHARES', message: '请输入有效的赎回份额' };
    }
    if (shares > holding.shares + 1e-9) {
      return { kind: 'error', code: 'EXCEEDS_SHARES', message: '超出可赎回份额' };
    }
    return this.makeRecord({
      kind: 'redemption',
      productId: holding.productId,
      holdingId,
      shares,
      requiredRisk: redemptionRisk(holding),
    });
  }

  private makeRecord(input: {
    kind: WealthOperation;
    productId: string;
    holdingId?: string;
    amountCents?: number;
    shares?: number;
    requiredRisk: 'red' | 'yellow';
  }): WealthPreviewResult {
    this.seq += 1;
    const approvalId = `${ID_PREFIX}${this.seq}`;
    const expiresAtMillis = this.scenario.planViewExpired ? this.now() - 60_000 : this.now() + 5 * 60_000;
    this.records.set(approvalId, {
      id: approvalId,
      expiresAtMillis,
      submitted: false,
      processed: false,
      finalStatus: null,
      afterUnknownStatus: null,
      operation: input.kind,
      productId: input.productId,
      holdingId: input.holdingId,
      amountCents: input.amountCents,
      shares: input.shares,
      requiredRisk: input.requiredRisk,
    });
    this.sync();
    return { kind: 'ok', approvalId };
  }

  /** 该审批所需的授权等级：以持久化记录为准，缺失时按 service 权威数据重算。 */
  private requiredRiskFor(rec: WealthRecord): 'red' | 'yellow' {
    if (rec.requiredRisk) return rec.requiredRisk;
    const product = BASE_PRODUCTS.find((p) => p.productId === rec.productId);
    if (rec.operation === 'purchase') {
      return product ? purchaseRisk(product, rec.amountCents ?? 0) : 'red';
    }
    const holding = computeHoldings(loadApplied(), false).find((h) => h.holdingId === rec.holdingId);
    if (holding) return redemptionRisk(holding);
    return product && (product.riskBand === 'R3' || product.riskBand === 'R4') ? 'red' : 'yellow';
  }

  private requireRecord(id: string): WealthRecord {
    const rec = this.records.get(id);
    if (!rec) throw new ApprovalError('APPROVAL_NOT_FOUND', '审批不存在或已失效，请重新预演');
    return rec;
  }

  async getPlanView(approvalId: string): Promise<WealthPlanView> {
    this.assertOnline();
    await this.sleep();
    if (this.scenario.planViewFailOnce && !this.planViewFailedOnce) {
      this.planViewFailedOnce = true;
      throw new ApprovalError('PLANVIEW_LOAD_FAILED', '预演详情加载失败，请重试');
    }
    const rec = this.requireRecord(approvalId);
    const product = BASE_PRODUCTS.find((p) => p.productId === rec.productId);
    const holdings = computeHoldings(loadApplied(), this.scenario.emptyHoldings);
    const holding = rec.holdingId ? holdings.find((h) => h.holdingId === rec.holdingId) : undefined;

    if (rec.operation === 'purchase') {
      const amountCents = rec.amountCents ?? 0;
      const riskLevel = this.requiredRiskFor(rec);
      const red = riskLevel === 'red';
      return {
        approvalId,
        riskLevel,
        operation: 'purchase',
        productId: rec.productId,
        productName: product?.name ?? '产品',
        riskBand: product?.riskBand ?? 'R1',
        amountCents,
        sourceText: '可用余额',
        feeText: '申购费率 0%（演示数据，不代表真实费率）',
        arrivalText: product?.termText ?? '',
        effectText: '确认后按服务端返回的净值确认份额，实际以服务端结果为准。',
        riskNotes: red
          ? [
              `该产品风险等级为 ${product?.riskBand ?? 'R1'}，净值可能波动。`,
              '历史业绩不代表未来表现，不构成任何收益承诺。',
              '请确认申购金额在你的可承受范围内。',
            ]
          : ['历史业绩不代表未来表现，不构成任何收益承诺。'],
        ackText: '我已阅读并理解上述风险揭示与产品说明',
        ackRequired: red,
        expiresAtMillis: rec.expiresAtMillis,
      };
    }

    const shares = rec.shares ?? 0;
    const netValue = holding?.netValue ?? product?.netValue ?? 1;
    const riskLevel = this.requiredRiskFor(rec);
    const red = riskLevel === 'red';
    return {
      approvalId,
      riskLevel,
      operation: 'redemption',
      productId: rec.productId,
      productName: holding?.name ?? product?.name ?? '持仓',
      riskBand: holding?.riskBand ?? product?.riskBand ?? 'R1',
      shares,
      estimatedAmountCents: Math.round(shares * netValue * 100),
      netValueForDisplay: netValue,
      sourceText: '赎回到可用余额',
      feeText: '赎回费率 0%（演示数据，不代表真实费率）',
      arrivalText: product?.termText ?? '',
      effectText: '赎回结果与到账时间以服务端返回为准。',
      riskNotes: red
        ? [`该持仓风险等级为 ${holding?.riskBand ?? 'R1'}，赎回金额随净值波动。`, '赎回后无法撤销，请确认份额无误。']
        : ['赎回后无法撤销，请确认份额无误。'],
      ackText: '我已阅读并理解上述赎回说明',
      ackRequired: red,
      expiresAtMillis: rec.expiresAtMillis,
    };
  }

  private applyOperation(rec: WealthRecord): void {
    const applied = loadApplied();
    if (rec.operation === 'purchase' && rec.amountCents) {
      applied.push({ kind: 'purchase', productId: rec.productId, amountCents: rec.amountCents });
    } else if (rec.operation === 'redemption' && rec.holdingId && rec.shares) {
      const holding = computeHoldings(loadApplied(), false).find((h) => h.holdingId === rec.holdingId);
      const estimatedAmountCents = Math.round((rec.shares ?? 0) * (holding?.netValue ?? 1) * 100);
      applied.push({
        kind: 'redemption',
        holdingId: rec.holdingId,
        shares: rec.shares,
        estimatedAmountCents,
      });
    }
    persistApplied(applied);
  }

  async confirmApproval(approvalId: string, authorization: ApprovalAuthorization): Promise<ConfirmResult> {
    this.assertOnline();
    await this.sleep(600);
    const rec = this.requireRecord(approvalId);

    // 权威校验：所需授权等级来自持久化审批记录（及 service 数据），不信任调用方声明。
    const required = this.requiredRiskFor(rec);
    if (required === 'red') {
      if (authorization.kind !== 'red') {
        throw new ApprovalError('AUTHORIZATION_REQUIRED', '该审批为红级操作，需要短信验证码授权');
      }
      if (authorization.otp.length !== 6) {
        throw new ApprovalError('OTP_INVALID', '请输入 6 位短信验证码');
      }
      if (this.scenario.otpErrorOnce && !this.otpErrorUsed) {
        this.otpErrorUsed = true;
        throw new ApprovalError('OTP_INVALID', '验证码不正确，还可尝试 2 次');
      }
    } else if (authorization.kind === 'red' && authorization.otp.length !== 6) {
      // 黄级审批接受更强的红级授权，但若已声明红级仍需提供完整验证码。
      throw new ApprovalError('OTP_INVALID', '请输入 6 位短信验证码');
    }
    if (rec.processed) throw new ApprovalError('APPROVAL_ALREADY_PROCESSED', '该审批已处理，请勿重复确认');
    if (rec.submitted) throw new ApprovalError('APPROVAL_ALREADY_SUBMITTED', '该审批已提交，请查询最新状态');
    if (this.now() > rec.expiresAtMillis) throw new ApprovalError('APPROVAL_EXPIRED', '预演已过期，请重新预演');

    switch (this.scenario.confirm) {
      case 'success': {
        rec.submitted = true;
        rec.processed = true;
        rec.finalStatus = 'success';
        this.applyOperation(rec);
        this.sync();
        return { status: 'success' };
      }
      case 'failedUncertain': {
        rec.submitted = true;
        rec.processed = true;
        rec.finalStatus = 'failed';
        rec.finalReason = undefined;
        this.sync();
        return { status: 'failed' };
      }
      case 'failedNoChange': {
        rec.submitted = true;
        rec.processed = true;
        rec.finalStatus = 'failed';
        rec.finalReason = '操作未完成，资产未发生变化';
        this.sync();
        return { status: 'failed', reason: rec.finalReason };
      }
      case 'unknown': {
        rec.submitted = true;
        rec.processed = false;
        rec.finalStatus = null;
        rec.afterUnknownStatus = this.scenario.afterUnknownStatus;
        this.sync();
        return { status: 'unknown' };
      }
      default: {
        rec.submitted = true;
        rec.processed = true;
        rec.finalStatus = 'success';
        this.applyOperation(rec);
        this.sync();
        return { status: 'success' };
      }
    }
  }

  async getApprovalStatus(approvalId: string): Promise<ApprovalStatus> {
    this.assertOnline();
    await this.sleep();
    const rec = this.requireRecord(approvalId);
    if (rec.processed && rec.finalStatus) return rec.finalStatus;
    if (rec.submitted && !rec.processed) {
      const outcome = rec.afterUnknownStatus ?? 'unknown';
      if (outcome === 'success' || outcome === 'failed') {
        rec.finalStatus = outcome;
        rec.processed = true;
        if (outcome === 'success') this.applyOperation(rec);
        this.sync();
      }
      return outcome;
    }
    return 'unknown';
  }

  async rejectApproval(_approvalId: string): Promise<void> {
    this.assertOnline();
    await this.sleep();
  }
}

/** 供页面/测试使用的分类定义（与 service 返回的 category 对应）。 */
export const PRODUCT_CATEGORY_LABELS: Array<{ value: 'all' | 'holding' | 'current' | 'steady' | 'fund'; label: string }> = [
  { value: 'all', label: '全部' },
  { value: 'holding', label: '持仓' },
  { value: 'current', label: '活期' },
  { value: 'steady', label: '稳健' },
  { value: 'fund', label: '基金' },
];

export type { RiskBand };
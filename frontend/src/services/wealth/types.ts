/** 理财领域类型。金额一律为整数「分」；份额、净值、比例有各自的格式化规则，不复用「分」。 */

export type RiskBand = 'R1' | 'R2' | 'R3' | 'R4';
export type TrendPeriod = '1M' | '3M' | '1Y';
export type RiskAssessmentStatus = 'valid' | 'missing' | 'expired';
export type ProductCategory = 'current' | 'steady' | 'fund' | 'gold';
export type WealthOperation = 'purchase' | 'redemption';

export interface TrendPoint {
  label: string;
  valueCents: number;
}

export interface WealthOverview {
  /** 投资总资产 = 持仓市值 + 可用余额 + 在途资金。 */
  totalAssetCents: number;
  todayPnlCents: number;
  holdingPnlCents: number;
  /** 持仓盈亏比例，比值（如 0.0018 表示 +0.18%）。 */
  holdingPnlPercent: number;
  availableBalanceCents: number;
  holdingValueCents: number;
  inTransitCents: number;
  yesterdayIncomeCents: number;
  trend: TrendPoint[];
  trendPeriod: TrendPeriod;
  riskAssessment: RiskAssessmentStatus;
  updatedAt: string;
  holdingsCount: number;
}

export interface WealthHolding {
  holdingId: string;
  productId: string;
  name: string;
  riskBand: RiskBand;
  marketValueCents: number;
  pnlCents: number;
  /** 持仓盈亏比例（比值）。 */
  pnlPercent: number;
  /** 占比（比值）。 */
  sharePercent: number;
  /** 单位净值（非金额，保留 4 位）。 */
  netValue: number;
  /** 份额（非金额）。 */
  shares: number;
  redeemable: boolean;
  redeemReason?: string;
}

export interface WealthProduct {
  productId: string;
  name: string;
  category: ProductCategory;
  riskBand: RiskBand;
  /** 起购金额（整数分）。 */
  minPurchaseCents: number;
  /** 期限或赎回到账说明。 */
  termText: string;
  onSale: boolean;
  purchasable: boolean;
  unavailableReason?: string;
  /** 明确的指标标签，不暗示未来收益。 */
  metricLabel: string;
  /** 指标展示文本，如「+2.46%」「1.1284」。 */
  metricText: string;
  /** 单位净值（非金额）。 */
  netValue: number;
  tags?: string[];
}

/** 申购 / 赎回的预演计划。riskLevel 与有效期由 service 决定。 */
export interface WealthPlanView {
  approvalId: string;
  /** 审批风险等级（黄/红），由 service 决定。 */
  riskLevel: 'yellow' | 'red';
  operation: WealthOperation;
  productId: string;
  productName: string;
  riskBand: RiskBand;
  /** 申购金额（整数分，仅 purchase）。 */
  amountCents?: number;
  /** 赎回份额（仅 redemption）。 */
  shares?: number;
  /** 预计赎回金额（整数分，仅 redemption，service 提供）。 */
  estimatedAmountCents?: number;
  /** 单位净值（非金额，仅 redemption，用于展示）。 */
  netValueForDisplay?: number;
  sourceText: string;
  feeText: string;
  arrivalText: string;
  effectText: string;
  riskNotes: string[];
  ackText: string;
  ackRequired: boolean;
  expiresAtMillis: number;
}

export type WealthPreviewResult =
  | { kind: 'ok'; approvalId: string }
  | { kind: 'error'; code: string; message: string };
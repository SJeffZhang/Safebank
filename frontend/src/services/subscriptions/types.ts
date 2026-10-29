import type { RiskLevel } from '../transfer/types';

export type SubscriptionStatus = 'active' | 'cancelled' | 'pending';

export interface Subscription {
  id: string;
  name: string;
  /** 图标的单字标识，如「视」。 */
  iconLabel: string;
  /** 扣款周期描述。 */
  cycleText: string;
  /** 下次扣款日期（展示用，如 11-05）。 */
  nextChargeDate: string;
  /** 下次扣款完整日期（可计算，YYYY-MM-DD），供未来 7 天待扣计算。 */
  nextChargeDateISO: string;
  /** 每期金额（整数分）。 */
  amountCents: number;
  /** 每期单位，如「/ 月」。 */
  amountUnit: string;
  status: SubscriptionStatus;
  /** 闲置提示（由 service 判定）。 */
  idle: boolean;
  /** 当前扣费前提醒时间，HH:mm。 */
  reminderTime: string;
}

export interface UpcomingCharges {
  /** 未来 7 天待扣总额（整数分，仅已入账/待扣 schedule）。 */
  upcomingTotalCents: number;
  /** 未来 7 天待扣笔数。 */
  upcomingCount: number;
}

export interface SubscriptionSummary {
  /** 本月订阅支出（整数分）。 */
  monthExpenseCents: number;
  /** 年化花费（整数分）。 */
  annualizedCents: number;
  /** 有效订阅数量。 */
  activeCount: number;
}

export type SubscriptionOperationKind = 'cancel' | 'reminder';

/** 取消/提醒修改的预演计划。 */
export interface SubscriptionPlanView {
  approvalId: string;
  riskLevel: RiskLevel;
  title: string;
  subscriptionName: string;
  operation: SubscriptionOperationKind;
  /** 当前扣款信息。 */
  billingText: string;
  /** 修改前的提醒时间（仅 reminder）。 */
  beforeReminder?: string;
  /** 修改后的提醒时间（仅 reminder）。 */
  afterReminder?: string;
  /** 生效说明。 */
  effectText: string;
  /** 留存理由 / 替代信息（service 返回，可为空）。 */
  retentionNote?: string;
  riskNotes: string[];
  expiresAtMillis: number;
}

export type SubscriptionPreviewResult =
  | { kind: 'ok'; approvalId: string }
  | { kind: 'error'; code: string; message: string };
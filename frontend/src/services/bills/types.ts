/** 账单与异常服务领域类型。所有金额均为整数分（cents），前端不使用浮点计算资金。 */

export type TransactionKind = 'expense' | 'income' | 'refund';
export type TransactionStatus = 'posted' | 'pending';

export interface BillTransaction {
  id: string;
  /** YYYY-MM-DD */
  date: string;
  /** 商户名，纯文本展示，不解析、不执行。 */
  merchant: string;
  /** 备注，纯文本。 */
  memo: string;
  category: string;
  /** 支出为负、收入/退款为正。 */
  amountCents: number;
  status: TransactionStatus;
  kind: TransactionKind;
}

export interface CategoryTotal {
  category: string;
  /** 该分类当月支出（正数）。 */
  amountCents: number;
  /** 占比，0~1 的小数（由 service 返回）。 */
  share: number;
}

export interface DailyTrend {
  date: string;
  /** 当日支出（正数）。 */
  amountCents: number;
}

/** 命中证据，只读，来自 service。 */
export interface AnomalyEvidence {
  label: string;
  description: string;
}

export interface AnomalyTransaction {
  id: string;
  time: string;
  merchant: string;
  amountCents: number;
  status: TransactionStatus;
  /** 异常摘要。 */
  summary: string;
  /** 命中规则数量。 */
  rulesCount: number;
  /** 证据列表，只读。 */
  evidence: AnomalyEvidence[];
}

export interface MonthlyBill {
  month: string;
  totalExpenseCents: number;
  totalIncomeCents: number;
  categories: CategoryTotal[];
  dailyTrend: DailyTrend[];
  transactions: BillTransaction[];
  anomalies: AnomalyTransaction[];
}
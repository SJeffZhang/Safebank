/** 首页聚合类型。金额一律整数分，数据来自各业务 service 的权威结果。 */

export interface HomeCustomer {
  customerId: string;
  name: string;
  avatarText: string;
  summary: string;
}

export interface HomeSummary {
  customer: HomeCustomer;
  /** 活期可用余额（分）。 */
  currentCents: number;
  /** 储蓄可用余额（分）。 */
  savingsCents: number;
  /** 总资产（分）= 活期 + 储蓄 + 计入的理财持仓市值。 */
  totalCents: number;
  /** 计入总资产的持仓产品数（未计入理财持仓的客户为 0）。 */
  holdingProductCount: number;
  /** 演示数据口径说明（是否为共享数据集）。 */
  dataNote: string;
}

export interface HomeUpcoming {
  count: number;
  totalCents: number;
  sourceSummary: string;
}

export interface HomeTransaction {
  id: string;
  name: string;
  time: string;
  category: string;
  amountCents: number;
  direction: 'in' | 'out';
}

export interface HomeBill {
  month: string;
  expenseCents: number;
}
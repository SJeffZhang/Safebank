import type { RiskLevel } from '../transfer/types';

export type CardStatus = 'normal' | 'locked' | 'reportedLost';
export type CardOperationKind = 'lock' | 'unlock' | 'onlinePayOn' | 'onlinePayOff' | 'reportLost';

export interface CardInfo {
  id: string;
  /** 展示名，例如「Everyday 借记卡」。 */
  name: string;
  /** mock 尾号，绝不展示完整卡号。 */
  last4: string;
  /** 省略卡号，例如「•••• •••• •••• 6094」。 */
  maskedNumber: string;
  /** 品牌标签。 */
  brand: string;
  /** 有效期，例如 08/29。 */
  expiry: string;
  /** 即将到期提示。 */
  expiringSoon: boolean;
  status: CardStatus;
  /** 线上支付是否开启。 */
  onlinePayEnabled: boolean;
  /** service 返回的可执行操作（前端不自行推断可操作性）。 */
  availableOperations: CardOperationKind[];
}

/** 卡片写操作的预演计划。 */
export interface CardPlanView {
  approvalId: string;
  riskLevel: RiskLevel;
  /** 操作对象标题，如「锁定卡片」。 */
  title: string;
  cardName: string;
  maskedNumber: string;
  /** 操作意图，如「锁定 / 解锁 / 挂失」。 */
  operationLabel: string;
  /** 生效说明。 */
  effectText: string;
  /** 风险提示（可为空）。 */
  riskNotes: string[];
  expiresAtMillis: number;
}

/** 预演结果。 */
export type CardPreviewResult =
  | { kind: 'ok'; approvalId: string }
  | { kind: 'error'; code: string; message: string };
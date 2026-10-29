/** 转账服务的领域类型。所有金额均为整数分（cents）。 */

export type RiskLevel = 'yellow' | 'red';
export type AccountStatus = 'normal' | 'frozen' | 'lost';

export interface TransferAccount {
  id: string;
  /** 账户名，如「活期」。 */
  name: string;
  /** 尾号，如「1234」。 */
  mask: string;
  /** 可用余额（分）。 */
  availableCents: number;
  status: AccountStatus;
  /** 本行标识文案。 */
  bankLabel?: string;
}

export interface TransferRecipient {
  id: string;
  name: string;
  /** 开户行标识，如「本行」。 */
  bankLabel: string;
  /** 账户尾号，如「8891」。 */
  mask: string;
}

export interface TransferInput {
  amountCents: number;
  fromAccountId: string;
  recipientId: string;
  /** 纯文本备注，不解析、不执行。 */
  memo: string;
}

export type PreviewResult =
  | { kind: 'ok'; approvalId: string }
  | { kind: 'error'; code: string; message: string };

export interface PlanView {
  approvalId: string;
  riskLevel: RiskLevel;
  title: string;
  recipientName: string;
  recipientAccount: string;
  fromAccount: string;
  amountCents: number;
  memo: string;
  availableBeforeCents: number;
  availableAfterCents: number;
  dailyTotalCents: number;
  reserved7dCents: number;
  feeCents: number;
  riskFindings: string[];
  expiresAtMillis: number;
}

export type ApprovalOutcome = 'success' | 'failed' | 'unknown';

export type ApprovalAuthorization =
  | { kind: 'yellow' }
  | { kind: 'red'; otp: string };

export interface ConfirmResult {
  status: ApprovalOutcome;
  /** failed 时的中性说明。 */
  reason?: string;
  /** 仅当 service 明确返回「未记账」时为 true，前端不得自行推断。 */
  noFundsMoved?: boolean;
}

export type ApprovalStatus = ApprovalOutcome;

export const DEMO_ACCOUNTS: TransferAccount[] = [
  { id: 'acc-current', name: '活期', mask: '1234', availableCents: 600000, status: 'normal', bankLabel: '本行' },
  { id: 'acc-savings', name: '储蓄', mask: '8891', availableCents: 2000000, status: 'normal', bankLabel: '本行' },
  { id: 'acc-frozen', name: '工资卡', mask: '5566', availableCents: 880000, status: 'frozen', bankLabel: '本行' },
  { id: 'acc-lost', name: '备用卡', mask: '7788', availableCents: 150000, status: 'lost', bankLabel: '本行' },
];

export const DEMO_RECIPIENTS: TransferRecipient[] = [
  { id: 'rcp-wang', name: '王五', bankLabel: '本行', mask: '8891' },
  { id: 'rcp-li', name: '李四', bankLabel: '本行', mask: '2410' },
  { id: 'rcp-sun', name: '孙八', bankLabel: '他行', mask: '9921' },
  // 与小安同音澄清候选共用同一份权威目录，保证选择后可以真正完成审批。
  { id: 'rcp-2210', name: '王武', bankLabel: '本行', mask: '2210' },
  { id: 'rcp-8873', name: '王伍', bankLabel: '本行', mask: '8873' },
];

/** 单笔 > 5000 元需要 OTP（红级）——由 mock service 决策并返回，不由前端自行推断。 */
export const RED_THRESHOLD_CENTS = 5000 * 100;
/** 单日累计限额（分）。 */
export const DAILY_LIMIT_CENTS = 100000 * 100;
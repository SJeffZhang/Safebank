/** 小安（智能体）领域类型。所有金额为整数分；所有文本按不可信纯文本渲染。 */

export type AssistantIntent = 'transfer' | 'subscriptionCancel' | 'cardLock' | 'wealthPurchase' | 'autoInvest' | 'unknown';

export type StepKey = 'understand' | 'lookup' | 'plan' | 'assess' | 'execute' | 'reply';

export type StepState =
  | 'done'
  | 'active'
  | 'passed'
  | 'insufficient'
  | 'needsClarification'
  | 'awaitingConfirm'
  | 'pending';

/** 可展示的依据（结论 / 来源 / 校验），不生成虚构的思维链。 */
export interface StepEvidence {
  label: string;
  value: string;
}

export interface AssistantStep {
  key: StepKey;
  label: string;
  state: StepState;
  stateText: string;
  evidence: StepEvidence[];
}

/** 可执行的业务目标参数（本原型仅转账可执行）。 */
export interface TransferTarget {
  kind: 'transfer';
  amountCents: number;
  fromAccountId: string;
  fromAccountName: string;
  recipientId: string;
  recipientName: string;
  recipientAccount: string;
  memo: string;
}

export interface AssistantPlan {
  planId: string;
  intent: AssistantIntent;
  title: string;
  steps: AssistantStep[];
  summary: string;
  fundingPath: string;
  feeNote: string;
  riskNote: string;
  target?: TransferTarget;
  /** 是否为当前原型支持执行的写操作（false 时不提供可点击的确认执行）。 */
  executable: boolean;
  unsupportedReason?: string;
}

export interface ClarificationCandidate {
  candidateId: string;
  name: string;
  bankLabel: string;
  /** 脱敏尾号，绝不展示完整账号。 */
  mask: string;
  /** 区分信息，如「最近转账 2025-08」。 */
  hint?: string;
}

export interface Clarification {
  clarificationId: string;
  question: string;
  reason: string;
  candidates: ClarificationCandidate[];
  allowManual: boolean;
}

export type AssistantReply =
  | { kind: 'plan'; plan: AssistantPlan; message: string }
  | { kind: 'clarification'; clarification: Clarification; message: string }
  | { kind: 'unplannable'; message: string; suggestions: string[] };

export interface AssistantHint {
  hintId: string;
  title: string;
  intent: AssistantIntent;
}

export interface InstructionInput {
  text?: string;
  /** 快捷指令只发送 hint_id，不回填文字。 */
  hintId?: string;
}
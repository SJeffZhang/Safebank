/** 演示认证领域类型。PIN 只在 service 内部比较，绝不写入存储或日志。 */

export type AuthErrorCode = 'PIN_INVALID' | 'LOCKED' | 'OFFLINE' | 'ERROR';

export type RiskAssessmentStatus = 'valid' | 'missing' | 'expired';

export interface DemoCustomer {
  customerId: string;
  name: string;
  /** 头像单字。 */
  avatarText: string;
  /** 一行摘要，如「R3 稳健型 · 理财持仓」。 */
  summary: string;
  riskBandLabel: string;
  /** 风险等级；未测评客户为 null。 */
  riskBand: 'R1' | 'R2' | 'R3' | 'R4' | null;
  riskAssessment: RiskAssessmentStatus;
  /** 脱敏手机号。 */
  maskedPhone: string;
  /** 首页聚合使用的演示账户 id。 */
  accountIds: string[];
  /** 是否把理财持仓计入首页总资产。 */
  includeWealthHoldings: boolean;
}

export interface AuthSessionPayload {
  customerId: string;
  customerName: string;
}

export type AuthResult =
  | { kind: 'ok'; session: AuthSessionPayload }
  | {
      kind: 'error';
      code: AuthErrorCode;
      message: string;
      /** 仅由 service 权威返回。 */
      remainingAttempts?: number;
      lockedUntilMillis?: number;
    };

export interface LockState {
  locked: boolean;
  lockedUntilMillis?: number;
  remainingAttempts: number;
}

export const DEMO_PIN_LENGTH = 6;
export const MAX_PIN_ATTEMPTS = 5;
export const LOCK_DURATION_MS = 60_000;
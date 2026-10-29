import type { AuthResult, DemoCustomer, LockState } from './types';
import { LOCK_DURATION_MS, MAX_PIN_ATTEMPTS } from './types';
import type { AuthService } from './AuthService';

export interface MockAuthScenario {
  offline: boolean;
}

export const DEFAULT_AUTH_SCENARIO: MockAuthScenario = { offline: false };

/** 演示客户目录（每位客户的 PIN 只保存在本文件内，不写入任何存储）。 */
const CUSTOMERS: Array<DemoCustomer & { pin: string }> = [
  {
    customerId: 'cust-zhang',
    name: '张三',
    avatarText: '张',
    summary: '无测评 · 活期 + 储蓄',
    riskBandLabel: '未测评',
    riskBand: null,
    riskAssessment: 'missing',
    maskedPhone: '138****1024',
    accountIds: ['acc-current', 'acc-savings'],
    includeWealthHoldings: false,
    pin: '111111',
  },
  {
    customerId: 'cust-li',
    name: '李四',
    avatarText: '李',
    summary: 'R3 稳健型 · 理财持仓',
    riskBandLabel: 'R3 稳健型',
    riskBand: 'R3',
    riskAssessment: 'valid',
    maskedPhone: '138****6602',
    accountIds: ['acc-current', 'acc-savings'],
    includeWealthHoldings: true,
    pin: '222222',
  },
  {
    customerId: 'cust-sun',
    name: '孙八',
    avatarText: '孙',
    summary: 'R2 平衡型 · 订阅与卡片',
    riskBandLabel: 'R2 平衡型',
    riskBand: 'R2',
    riskAssessment: 'valid',
    maskedPhone: '139****3388',
    accountIds: ['acc-savings'],
    includeWealthHoldings: false,
    pin: '333333',
  },
];

const STATE_KEY = 'mock-auth-state';

interface AttemptState {
  attempts: number;
  lockedUntilMillis?: number;
}

/** 只持久化错误计数与锁定截止时间，绝不保存 PIN。 */
function loadState(): Record<string, AttemptState> {
  try {
    const raw = window.sessionStorage.getItem(STATE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

function persistState(state: Record<string, AttemptState>): void {
  try {
    window.sessionStorage.setItem(STATE_KEY, JSON.stringify(state));
  } catch {
    /* ignore */
  }
}

/** 退出登录 / 测试用：清除认证计数（正常登录不会调用）。 */
export function clearMockAuthState(): void {
  try {
    window.sessionStorage.removeItem(STATE_KEY);
  } catch {
    /* ignore */
  }
}

export class MockAuthService implements AuthService {
  private scenario: MockAuthScenario;
  private delayMs: number;
  private now: () => number;

  constructor(scenario?: Partial<MockAuthScenario>, delayMs = 300, now: () => number = () => Date.now()) {
    this.scenario = { ...DEFAULT_AUTH_SCENARIO, ...scenario };
    this.delayMs = delayMs;
    this.now = now;
  }

  getScenario(): MockAuthScenario {
    return { ...this.scenario };
  }

  setScenario(patch: Partial<MockAuthScenario>): void {
    this.scenario = { ...this.scenario, ...patch };
  }

  private sleep(ms = this.delayMs): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  async getCustomers(): Promise<DemoCustomer[]> {
    if (this.scenario.offline) {
      throw Object.assign(new Error('网络不可用'), { code: 'OFFLINE' as const });
    }
    await this.sleep();
    return CUSTOMERS.map(({ pin: _pin, ...rest }) => ({ ...rest }));
  }

  async getLockState(customerId: string): Promise<LockState> {
    const state = loadState();
    const entry = state[customerId] ?? { attempts: 0 };
    if (entry.lockedUntilMillis && entry.lockedUntilMillis <= this.now()) {
      // 锁定已到期：按 service 权威状态恢复
      state[customerId] = { attempts: 0 };
      persistState(state);
      return { locked: false, remainingAttempts: MAX_PIN_ATTEMPTS };
    }
    if (entry.lockedUntilMillis) {
      return { locked: true, lockedUntilMillis: entry.lockedUntilMillis, remainingAttempts: 0 };
    }
    return { locked: false, remainingAttempts: Math.max(0, MAX_PIN_ATTEMPTS - entry.attempts) };
  }

  async submitPin(customerId: string, pin: string): Promise<AuthResult> {
    if (this.scenario.offline) {
      return { kind: 'error', code: 'OFFLINE', message: '当前离线，无法登录' };
    }
    await this.sleep();

    const customer = CUSTOMERS.find((c) => c.customerId === customerId);
    if (!customer) {
      return { kind: 'error', code: 'ERROR', message: '客户不存在' };
    }

    const state = loadState();
    const entry: AttemptState = state[customerId] ?? { attempts: 0 };

    // 锁定优先：到期后按权威状态恢复
    if (entry.lockedUntilMillis) {
      if (entry.lockedUntilMillis > this.now()) {
        return {
          kind: 'error',
          code: 'LOCKED',
          message: '连续多次错误，账户已临时锁定',
          remainingAttempts: 0,
          lockedUntilMillis: entry.lockedUntilMillis,
        };
      }
      entry.attempts = 0;
      delete entry.lockedUntilMillis;
    }

    if (pin === customer.pin) {
      state[customerId] = { attempts: 0 };
      persistState(state);
      return { kind: 'ok', session: { customerId: customer.customerId, customerName: customer.name } };
    }

    entry.attempts += 1;
    if (entry.attempts >= MAX_PIN_ATTEMPTS) {
      entry.lockedUntilMillis = this.now() + LOCK_DURATION_MS;
      state[customerId] = entry;
      persistState(state);
      return {
        kind: 'error',
        code: 'LOCKED',
        message: '连续 5 次错误，账户已临时锁定',
        remainingAttempts: 0,
        lockedUntilMillis: entry.lockedUntilMillis,
      };
    }
    state[customerId] = entry;
    persistState(state);
    return {
      kind: 'error',
      code: 'PIN_INVALID',
      message: 'PIN 不正确',
      remainingAttempts: Math.max(0, MAX_PIN_ATTEMPTS - entry.attempts),
    };
  }
}
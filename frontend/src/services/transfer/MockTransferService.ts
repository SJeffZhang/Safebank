import type {
  ApprovalAuthorization,
  ApprovalStatus,
  ConfirmResult,
  PlanView,
  PreviewResult,
  TransferAccount,
  TransferInput,
  TransferRecipient,
} from './types';
import { DAILY_LIMIT_CENTS, DEMO_ACCOUNTS, DEMO_RECIPIENTS, RED_THRESHOLD_CENTS } from './types';
import { formatYuan } from './money';
import { OfflineError, TransferError } from './TransferService';
import type { TransferService } from './TransferService';

export type MockPreviewOutcome = 'ok' | 'insufficient' | 'frozen' | 'overLimit' | 'fail';
export type MockConfirmOutcome = 'success' | 'failedNoFunds' | 'failedUncertain' | 'unknown';
export type MockStatusOutcome = 'success' | 'failed' | 'unknown';

export interface MockScenario {
  preview: MockPreviewOutcome;
  /** 首次 getPlanView 失败一次（用于演示重试）。 */
  planViewFailOnce: boolean;
  /** PlanView 已过期（预演即返回过期时间）。 */
  planViewExpired: boolean;
  confirm: MockConfirmOutcome;
  /** UNKNOWN 之后调用 getApprovalStatus 返回的结果。 */
  afterUnknownStatus: MockStatusOutcome;
  /** 模拟断网：所有网络调用抛 OfflineError。 */
  offline: boolean;
}

export const DEFAULT_SCENARIO: MockScenario = {
  preview: 'ok',
  planViewFailOnce: false,
  planViewExpired: false,
  confirm: 'success',
  afterUnknownStatus: 'unknown',
  offline: false,
};

export const DEMO_DAILY_REMAINING_CENTS = 24000 * 100;

/**
 * 每条审批记录持久化的字段（仅演示用的审批上下文，不是真实事实数据）。
 * 不保存 OTP、客户敏感信息或真实金融事实。
 */
interface MockRecord {
  id: string;
  input: TransferInput;
  expiresAtMillis: number;
  /** 是否已提交过确认（confirmApproval 已调用且结果为 UNKNOWN）。 */
  submitted: boolean;
  /** 是否已处理完成（有最终 SUCCESS / FAILED 状态）。 */
  processed: boolean;
  /** 最终状态；未最终时为空。 */
  finalStatus: ApprovalStatus | null;
  finalReason?: string;
  finalNoFundsMoved?: boolean;
  /** UNKNOWN 提交时快照的“后续应查询到的结果”。 */
  afterUnknownStatus: ApprovalStatus | null;
}

/** 明确命名的 mock 演示记录（仅用于刷新后演示查询，不是真实事实数据，退出时清除）。 */
const RECORDS_KEY = 'mock-transfer-records';
/** 账户余额状态：成功审批的实际扣款结果（与审批记录同源，退出时一并清除）。 */
const BALANCES_KEY = 'mock-transfer-balances';

interface BalanceState {
  /** 账户 id → 可用余额（分）。 */
  balances: Record<string, number>;
  /** 已应用扣款的 approval_id，保证 SUCCESS 只扣一次。 */
  applied: string[];
}

function loadBalanceState(): BalanceState {
  try {
    const raw = window.sessionStorage.getItem(BALANCES_KEY);
    if (!raw) return { balances: {}, applied: [] };
    const parsed = JSON.parse(raw) as Partial<BalanceState>;
    return {
      balances: parsed.balances && typeof parsed.balances === 'object' ? parsed.balances : {},
      applied: Array.isArray(parsed.applied) ? parsed.applied : [],
    };
  } catch {
    return { balances: {}, applied: [] };
  }
}

function persistBalanceState(state: BalanceState): void {
  try {
    window.sessionStorage.setItem(BALANCES_KEY, JSON.stringify(state));
  } catch {
    /* ignore */
  }
}

function normalizeRecord(rec: Partial<MockRecord>): MockRecord {
  return {
    id: rec.id ?? '',
    input: rec.input ?? { amountCents: 0, fromAccountId: '', recipientId: '', memo: '' },
    expiresAtMillis: rec.expiresAtMillis ?? 0,
    submitted: rec.submitted ?? false,
    processed: rec.processed ?? false,
    finalStatus: rec.finalStatus ?? null,
    finalReason: rec.finalReason,
    finalNoFundsMoved: rec.finalNoFundsMoved,
    afterUnknownStatus: rec.afterUnknownStatus ?? null,
  };
}

function loadRecords(): MockRecord[] {
  try {
    const raw = window.sessionStorage.getItem(RECORDS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.map((r) => normalizeRecord(r as Partial<MockRecord>));
  } catch {
    return [];
  }
}

function persistRecords(records: MockRecord[]): void {
  try {
    window.sessionStorage.setItem(RECORDS_KEY, JSON.stringify(records));
  } catch {
    /* ignore */
  }
}

/** 清除 mock 演示记录（退出登录时调用）。 */
export function clearMockTransferRecords(): void {
  try {
    window.sessionStorage.removeItem(RECORDS_KEY);
    window.sessionStorage.removeItem(BALANCES_KEY);
  } catch {
    /* ignore */
  }
}

/**
 * 演示用转账/审批服务（内存实现，明确标注为 mock）。
 * 所有金额使用整数分；延迟用 delayMs 控制（测试可置 0）；
 * now 可注入以在测试中推进时间模拟过期。
 * 审批结果跟随各自的 approval_id 记录持久化，刷新后的新实例使用默认场景也能恢复原审批结果。
 */
export class MockTransferService implements TransferService {
  private scenario: MockScenario;
  private delayMs: number;
  private now: () => number;
  private seq = 0;
  private planViewFailedOnce = false;
  private records = new Map<string, MockRecord>();
  /** 已被扣款的账户余额（分）。 */
  private balances = new Map<string, number>();
  /** 已应用扣款的审批 id，保证幂等。 */
  private applied = new Set<string>();

  constructor(scenario?: Partial<MockScenario>, delayMs = 300, now: () => number = () => Date.now()) {
    this.scenario = { ...DEFAULT_SCENARIO, ...scenario };
    this.delayMs = delayMs;
    this.now = now;

    // 恢复记录，并从已有 ID 推断下一个可用序号，避免刷新后覆盖旧审批。
    let maxSeq = 0;
    for (const rec of loadRecords()) {
      this.records.set(rec.id, rec);
      const m = /^mock-apv-(\d+)$/.exec(rec.id);
      if (m) maxSeq = Math.max(maxSeq, Number(m[1]));
    }
    this.seq = maxSeq;

    // 恢复已应用的余额变更（与审批记录同一份演示事实，刷新后不回到初始余额）。
    const stored = loadBalanceState();
    for (const [accountId, cents] of Object.entries(stored.balances)) {
      if (Number.isInteger(cents) && cents >= 0) this.balances.set(accountId, cents);
    }
    for (const id of stored.applied) this.applied.add(id);
  }

  /** 权威账户快照：DEMO_ACCOUNTS 与已应用扣款合并后的结果。 */
  private accountsSnapshot(): TransferAccount[] {
    return DEMO_ACCOUNTS.map((account) => ({
      ...account,
      availableCents: this.balances.get(account.id) ?? account.availableCents,
    }));
  }

  /** 应用一次扣款；同一 approval_id 只生效一次。 */
  private applyDebit(rec: MockRecord): void {
    if (this.applied.has(rec.id)) return;
    const account = this.accountsSnapshot().find((a) => a.id === rec.input.fromAccountId);
    if (!account) return;
    const nextCents = account.availableCents - rec.input.amountCents;
    if (nextCents < 0) return; // 余额不足时不产生扣款
    this.balances.set(account.id, nextCents);
    this.applied.add(rec.id);
    persistBalanceState({
      balances: Object.fromEntries(this.balances),
      applied: Array.from(this.applied),
    });
  }

  getScenario(): MockScenario {
    return { ...this.scenario };
  }

  setScenario(patch: Partial<MockScenario>): void {
    this.scenario = { ...this.scenario, ...patch };
  }

  private sleep(ms = this.delayMs): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  private assertOnline(): void {
    if (this.scenario.offline) throw new OfflineError();
  }

  private syncRecords(): void {
    persistRecords(Array.from(this.records.values()));
  }

  async listAccounts(): Promise<TransferAccount[]> {
    this.assertOnline();
    await this.sleep();
    return this.accountsSnapshot();
  }

  async listRecipients(): Promise<TransferRecipient[]> {
    this.assertOnline();
    await this.sleep();
    return DEMO_RECIPIENTS.map((r) => ({ ...r }));
  }

  async getDailyRemainingCents(): Promise<number> {
    this.assertOnline();
    await this.sleep();
    return DEMO_DAILY_REMAINING_CENTS;
  }

  async previewTransfer(input: TransferInput): Promise<PreviewResult> {
    this.assertOnline();
    await this.sleep();

    const account = this.accountsSnapshot().find((a) => a.id === input.fromAccountId);
    if (!account) {
      return { kind: 'error', code: 'NO_ACCOUNT', message: '请选择付款账户' };
    }

    // 收款人必须是权威目录中的真实条目，绝不回退到其他收款人。
    const recipient = DEMO_RECIPIENTS.find((r) => r.id === input.recipientId);
    if (!recipient) {
      return { kind: 'error', code: 'RECIPIENT_NOT_FOUND', message: '收款人不存在或已失效，请重新选择' };
    }

    if (account.status === 'frozen' || account.status === 'lost') {
      return { kind: 'error', code: 'ACCOUNT_PAUSED', message: '该账户已暂停外转' };
    }

    if (this.scenario.preview === 'insufficient' || input.amountCents > account.availableCents) {
      return {
        kind: 'error',
        code: 'INSUFFICIENT',
        message: `余额不足：可用 ${formatYuan(account.availableCents)}`,
      };
    }

    if (this.scenario.preview === 'frozen') {
      return { kind: 'error', code: 'ACCOUNT_PAUSED', message: '该账户已暂停外转' };
    }

    if (this.scenario.preview === 'overLimit' || input.amountCents > DAILY_LIMIT_CENTS) {
      return { kind: 'error', code: 'OVER_LIMIT', message: '超过单日累计限额' };
    }

    if (this.scenario.preview === 'fail') {
      return { kind: 'error', code: 'PREVIEW_FAILED', message: '预演失败，请稍后重试' };
    }

    this.seq += 1;
    const approvalId = `mock-apv-${this.seq}`;
    const expiresAtMillis = this.scenario.planViewExpired
      ? this.now() - 60_000
      : this.now() + 5 * 60_000;
    this.records.set(approvalId, {
      id: approvalId,
      input: { ...input },
      expiresAtMillis,
      submitted: false,
      processed: false,
      finalStatus: null,
      afterUnknownStatus: null,
    });
    this.syncRecords();
    return { kind: 'ok', approvalId };
  }

  private requireRecord(approvalId: string): MockRecord {
    const rec = this.records.get(approvalId);
    if (!rec) {
      throw new TransferError('APPROVAL_NOT_FOUND', '审批不存在或已失效，请重新预演');
    }
    return rec;
  }

  async getPlanView(approvalId: string): Promise<PlanView> {
    this.assertOnline();
    await this.sleep();

    if (this.scenario.planViewFailOnce && !this.planViewFailedOnce) {
      this.planViewFailedOnce = true;
      throw new TransferError('PLANVIEW_LOAD_FAILED', '预演详情加载失败，请重试');
    }

    const rec = this.requireRecord(approvalId);
    const account = this.accountsSnapshot().find((a) => a.id === rec.input.fromAccountId);
    if (!account) {
      throw new TransferError('NO_ACCOUNT', '付款账户不存在，请重新预演');
    }
    const recipient = DEMO_RECIPIENTS.find((r) => r.id === rec.input.recipientId);
    if (!recipient) {
      throw new TransferError('RECIPIENT_NOT_FOUND', '收款人不存在或已失效，请重新预演');
    }
    const amountCents = rec.input.amountCents;
    const availableBefore = account.availableCents;
    const riskLevel = amountCents > RED_THRESHOLD_CENTS ? 'red' : 'yellow';

    return {
      approvalId,
      riskLevel,
      title: `转给${recipient.name}`,
      recipientName: recipient.name,
      recipientAccount: `${recipient.bankLabel} · ${recipient.mask}`,
      fromAccount: `${account.name} · 尾号 ${account.mask}`,
      amountCents,
      memo: rec.input.memo.trim() === '' ? '无备注' : rec.input.memo,
      availableBeforeCents: availableBefore,
      availableAfterCents: availableBefore - amountCents,
      dailyTotalCents: amountCents,
      reserved7dCents: 255000,
      feeCents: 0,
      riskFindings:
        riskLevel === 'red' ? ['单笔超过 5,000 元，需短信验证码（OTP）'] : ['2 分钟内第 2 笔 · 规则 R2-DAILY'],
      expiresAtMillis: rec.expiresAtMillis,
    };
  }

  async confirmApproval(approvalId: string, authorization: ApprovalAuthorization): Promise<ConfirmResult> {
    this.assertOnline();
    await this.sleep(600);

    if (authorization.kind === 'red' && authorization.otp.length !== 6) {
      throw new TransferError('OTP_INVALID', '验证码不正确');
    }

    const rec = this.requireRecord(approvalId);
    if (rec.processed) {
      throw new TransferError('APPROVAL_ALREADY_PROCESSED', '该审批已处理，请勿重复确认');
    }
    if (rec.submitted) {
      // 已提交但结果未知：不允许复用原 OTP/授权再次确认，应通过 getApprovalStatus 查询。
      throw new TransferError('APPROVAL_ALREADY_SUBMITTED', '该审批已提交，请查询最新状态');
    }
    if (this.now() > rec.expiresAtMillis) {
      throw new TransferError('APPROVAL_EXPIRED', '预演已过期，请重新预演');
    }

    switch (this.scenario.confirm) {
      case 'success': {
        rec.submitted = true;
        rec.processed = true;
        rec.finalStatus = 'success';
        this.applyDebit(rec);
        this.syncRecords();
        return { status: 'success' };
      }
      case 'failedNoFunds': {
        rec.submitted = true;
        rec.processed = true;
        rec.finalStatus = 'failed';
        rec.finalReason = '收款账户状态异常，交易未完成';
        rec.finalNoFundsMoved = true;
        this.syncRecords();
        return { status: 'failed', reason: rec.finalReason, noFundsMoved: true };
      }
      case 'failedUncertain': {
        rec.submitted = true;
        rec.processed = true;
        rec.finalStatus = 'failed';
        rec.finalReason = '交易状态不明，请查看详情后决定下一步';
        this.syncRecords();
        return { status: 'failed', reason: rec.finalReason };
      }
      case 'unknown': {
        rec.submitted = true;
        rec.processed = false;
        rec.finalStatus = null;
        rec.afterUnknownStatus = this.scenario.afterUnknownStatus; // 快照此时场景的后续结果
        this.syncRecords();
        return { status: 'unknown' };
      }
      default: {
        rec.submitted = true;
        rec.processed = true;
        rec.finalStatus = 'success';
        this.applyDebit(rec);
        this.syncRecords();
        return { status: 'success' };
      }
    }
  }

  async getApprovalStatus(approvalId: string): Promise<ApprovalStatus> {
    this.assertOnline();
    await this.sleep();
    const rec = this.requireRecord(approvalId); // 未知 ID 明确报错

    if (rec.processed && rec.finalStatus) {
      if (rec.finalStatus === 'success') this.applyDebit(rec); // 幂等：旧记录恢复时补齐扣款
      return rec.finalStatus;
    }

    if (rec.submitted && !rec.processed) {
      const outcome = rec.afterUnknownStatus ?? 'unknown';
      if (outcome === 'success' || outcome === 'failed') {
        rec.finalStatus = outcome;
        rec.processed = true;
        if (outcome === 'success') this.applyDebit(rec); // UNKNOWN 查询到成功时只扣一次
        this.syncRecords(); // 到达最终状态后持久化
      }
      return outcome;
    }

    // 已存在但未提交确认的审批：保守返回 unknown。
    return 'unknown';
  }

  async rejectApproval(_approvalId: string): Promise<void> {
    this.assertOnline();
    await this.sleep();
  }
}
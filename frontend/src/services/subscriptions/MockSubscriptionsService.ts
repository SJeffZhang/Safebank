import type { ApprovalAuthorization, ApprovalStatus, ConfirmResult } from '../transfer/types';
import type {
  Subscription,
  SubscriptionPlanView,
  SubscriptionPreviewResult,
  SubscriptionSummary,
  SubscriptionOperationKind,
  UpcomingCharges,
} from './types';
import {
  ApprovalError,
  GatewayOfflineError,
  clearRecords,
  maxSeqFromIds,
  persistRecords,
  type PersistedRecord,
} from '../approval/shared';
import type { SubscriptionsService } from './SubscriptionsService';

export type MockSubsConfirmOutcome = 'success' | 'failedUncertain' | 'failedNoChange' | 'unknown';
export type MockSubsStatusOutcome = 'success' | 'failed' | 'unknown';

export interface MockSubscriptionsScenario {
  confirm: MockSubsConfirmOutcome;
  afterUnknownStatus: MockSubsStatusOutcome;
  /** 取消的审批风险等级由 service 返回。 */
  cancelRisk: 'yellow' | 'red';
  planViewFailOnce: boolean;
  planViewExpired: boolean;
  offline: boolean;
  /** 返回空订阅列表。 */
  emptyList: boolean;
  /** 首次 listSubscriptions 失败一次。 */
  listFailOnce: boolean;
}

export const DEFAULT_SUBS_SCENARIO: MockSubscriptionsScenario = {
  confirm: 'success',
  afterUnknownStatus: 'unknown',
  cancelRisk: 'yellow',
  planViewFailOnce: false,
  planViewExpired: false,
  offline: false,
  emptyList: false,
  listFailOnce: false,
};

const RECORDS_KEY = 'mock-subs-records';
const STATE_KEY = 'mock-subs-state';
const ID_PREFIX = 'mock-sub-apv-';

interface SubRecord extends PersistedRecord {
  subscriptionId: string;
  operation: SubscriptionOperationKind;
  beforeReminder?: string;
  afterReminder?: string;
}

const BASE_SUBSCRIPTIONS: Subscription[] = [
  {
    id: 's1',
    name: '视频会员',
    iconLabel: '视',
    cycleText: '每月',
    nextChargeDate: '11-05',
    nextChargeDateISO: '2026-11-05',
    amountCents: 2500,
    amountUnit: '/ 月',
    status: 'active',
    idle: false,
    reminderTime: '09:00',
  },
  {
    id: 's2',
    name: '云音乐',
    iconLabel: '音',
    cycleText: '每月',
    nextChargeDate: '11-02',
    nextChargeDateISO: '2026-11-02',
    amountCents: 1500,
    amountUnit: '/ 月',
    status: 'active',
    idle: false,
    reminderTime: '08:00',
  },
  {
    id: 's3',
    name: '云存储',
    iconLabel: '存',
    cycleText: '每月',
    nextChargeDate: '11-08',
    nextChargeDateISO: '2026-11-08',
    amountCents: 600,
    amountUnit: '/ 月',
    status: 'active',
    idle: false,
    reminderTime: '10:00',
  },
  {
    id: 's4',
    name: '健身私教',
    iconLabel: '健',
    cycleText: '每月',
    nextChargeDate: '11-12',
    nextChargeDateISO: '2026-11-12',
    amountCents: 21900,
    amountUnit: '/ 月',
    status: 'active',
    idle: true,
    reminderTime: '09:30',
  },
];

type SubStateOverride = { status?: Subscription['status']; reminderTime?: string };

function loadState(): Record<string, SubStateOverride> {
  try {
    const raw = window.sessionStorage.getItem(STATE_KEY);
    return raw ? (JSON.parse(raw) as Record<string, SubStateOverride>) : {};
  } catch {
    return {};
  }
}

function persistState(state: Record<string, SubStateOverride>): void {
  try {
    window.sessionStorage.setItem(STATE_KEY, JSON.stringify(state));
  } catch {
    /* ignore */
  }
}

export function clearMockSubscriptionsRecords(): void {
  clearRecords(RECORDS_KEY);
  try {
    window.sessionStorage.removeItem(STATE_KEY);
  } catch {
    /* ignore */
  }
}

function normalize(rec: Partial<SubRecord>): SubRecord {
  return {
    id: rec.id ?? '',
    expiresAtMillis: rec.expiresAtMillis ?? 0,
    submitted: rec.submitted ?? false,
    processed: rec.processed ?? false,
    finalStatus: rec.finalStatus ?? null,
    finalReason: rec.finalReason,
    noFundsMoved: rec.noFundsMoved,
    afterUnknownStatus: rec.afterUnknownStatus ?? null,
    subscriptionId: rec.subscriptionId ?? '',
    operation: rec.operation ?? 'cancel',
    beforeReminder: rec.beforeReminder,
    afterReminder: rec.afterReminder,
  };
}

function loadSubRecords(): SubRecord[] {
  try {
    const raw = window.sessionStorage.getItem(RECORDS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.map((r) => normalize(r as Partial<SubRecord>)) : [];
  } catch {
    return [];
  }
}

export class MockSubscriptionsService implements SubscriptionsService {
  private scenario: MockSubscriptionsScenario;
  private delayMs: number;
  private now: () => number;
  private seq = 0;
  private planViewFailedOnce = false;
  private listFailedOnce = false;
  private records = new Map<string, SubRecord>();

  constructor(scenario?: Partial<MockSubscriptionsScenario>, delayMs = 300, now: () => number = () => Date.now()) {
    this.scenario = { ...DEFAULT_SUBS_SCENARIO, ...scenario };
    this.delayMs = delayMs;
    this.now = now;
    const recs = loadSubRecords();
    this.seq = maxSeqFromIds(recs, ID_PREFIX);
    for (const r of recs) this.records.set(r.id, r);
  }

  getScenario(): MockSubscriptionsScenario {
    return { ...this.scenario };
  }

  setScenario(patch: Partial<MockSubscriptionsScenario>): void {
    this.scenario = { ...this.scenario, ...patch };
  }

  private sleep(ms = this.delayMs): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  private assertOnline(): void {
    if (this.scenario.offline) throw new GatewayOfflineError();
  }

  private sync(): void {
    persistRecords(RECORDS_KEY, Array.from(this.records.values()));
  }

  private computeSubscriptions(): Subscription[] {
    const state = loadState();
    return BASE_SUBSCRIPTIONS.map((s) => ({
      ...s,
      status: state[s.id]?.status ?? s.status,
      reminderTime: state[s.id]?.reminderTime ?? s.reminderTime,
    }));
  }

  private applyOperation(subscriptionId: string, op: SubRecord): void {
    const state = loadState();
    const cur = state[subscriptionId] ?? {};
    if (op.operation === 'cancel') cur.status = 'cancelled';
    else if (op.operation === 'reminder' && op.afterReminder) cur.reminderTime = op.afterReminder;
    state[subscriptionId] = cur;
    persistState(state);
  }

  private requireRecord(id: string): SubRecord {
    const rec = this.records.get(id);
    if (!rec) throw new ApprovalError('APPROVAL_NOT_FOUND', '审批不存在或已失效，请重新预演');
    return rec;
  }

  private listItems(): Subscription[] {
    if (this.scenario.emptyList) return [];
    return this.computeSubscriptions();
  }

  async listSubscriptions(): Promise<Subscription[]> {
    this.assertOnline();
    await this.sleep();
    if (this.scenario.listFailOnce && !this.listFailedOnce) {
      this.listFailedOnce = true;
      throw new Error('模拟列表加载失败');
    }
    return this.listItems();
  }

  async getUpcomingCharges(): Promise<UpcomingCharges> {
    this.assertOnline();
    await this.sleep();
    const active = this.listItems().filter((s) => s.status === 'active');
    const nowMs = this.now();
    const today = new Date(nowMs);
    const todayStart = Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate());
    const dayMs = 24 * 60 * 60 * 1000;
    const within7 = active.filter((s) => {
      const chargeMs = Date.parse(`${s.nextChargeDateISO}T00:00:00Z`);
      if (Number.isNaN(chargeMs)) return false;
      return chargeMs >= todayStart && chargeMs < todayStart + 7 * dayMs;
    });
    return {
      upcomingTotalCents: within7.reduce((a, s) => a + s.amountCents, 0),
      upcomingCount: within7.length,
    };
  }

  async getSummary(): Promise<SubscriptionSummary> {
    this.assertOnline();
    await this.sleep();
    const active = this.listItems().filter((s) => s.status === 'active');
    const monthExpense = active.reduce((a, s) => a + s.amountCents, 0);
    return {
      monthExpenseCents: monthExpense,
      annualizedCents: monthExpense * 12,
      activeCount: active.length,
    };
  }

  async prepareCancellation(subscriptionId: string): Promise<SubscriptionPreviewResult> {
    this.assertOnline();
    await this.sleep();
    const sub = this.computeSubscriptions().find((s) => s.id === subscriptionId);
    if (!sub) return { kind: 'error', code: 'NO_SUBSCRIPTION', message: '订阅不存在' };
    if (sub.status !== 'active') return { kind: 'error', code: 'NOT_ACTIVE', message: '该订阅已取消，无需重复操作' };
    return this.makeRecord(subscriptionId, 'cancel', undefined, undefined);
  }

  async prepareReminderChange(subscriptionId: string, reminderTime: string): Promise<SubscriptionPreviewResult> {
    this.assertOnline();
    await this.sleep();
    const sub = this.computeSubscriptions().find((s) => s.id === subscriptionId);
    if (!sub) return { kind: 'error', code: 'NO_SUBSCRIPTION', message: '订阅不存在' };
    if (sub.status !== 'active') return { kind: 'error', code: 'NOT_ACTIVE', message: '该订阅已取消，无法修改提醒' };
    if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(reminderTime)) {
      return { kind: 'error', code: 'INVALID_TIME', message: '时间格式不正确，请使用 HH:mm' };
    }
    return this.makeRecord(subscriptionId, 'reminder', sub.reminderTime, reminderTime);
  }

  private makeRecord(
    subscriptionId: string,
    operation: SubscriptionOperationKind,
    beforeReminder?: string,
    afterReminder?: string,
  ): SubscriptionPreviewResult {
    this.seq += 1;
    const approvalId = `${ID_PREFIX}${this.seq}`;
    const expiresAtMillis = this.scenario.planViewExpired ? this.now() - 60_000 : this.now() + 5 * 60_000;
    this.records.set(approvalId, {
      id: approvalId,
      expiresAtMillis,
      submitted: false,
      processed: false,
      finalStatus: null,
      afterUnknownStatus: null,
      subscriptionId,
      operation,
      beforeReminder,
      afterReminder,
    });
    this.sync();
    return { kind: 'ok', approvalId };
  }

  async getPlanView(approvalId: string): Promise<SubscriptionPlanView> {
    this.assertOnline();
    await this.sleep();
    if (this.scenario.planViewFailOnce && !this.planViewFailedOnce) {
      this.planViewFailedOnce = true;
      throw new ApprovalError('PLANVIEW_LOAD_FAILED', '预演详情加载失败，请重试');
    }
    const rec = this.requireRecord(approvalId);
    const sub = BASE_SUBSCRIPTIONS.find((s) => s.id === rec.subscriptionId);
    const riskLevel = rec.operation === 'cancel' ? this.scenario.cancelRisk : 'yellow';
    return {
      approvalId,
      riskLevel,
      title: rec.operation === 'cancel' ? '取消订阅' : '修改提醒时间',
      subscriptionName: sub?.name ?? '订阅',
      operation: rec.operation,
      billingText: sub ? `${sub.cycleText} ${sub.nextChargeDate} 扣款` : '',
      beforeReminder: rec.beforeReminder,
      afterReminder: rec.afterReminder,
      effectText:
        rec.operation === 'cancel'
          ? '取消后不再自动扣款。'
          : `扣费提醒时间将调整为 ${rec.afterReminder ?? ''}。`,
      retentionNote:
        rec.operation === 'cancel' && sub?.idle ? '该订阅已 90 天未使用，可考虑取消。' : undefined,
      riskNotes:
        rec.operation === 'cancel'
          ? ['取消需要确认，确认后订阅将停止自动续费。']
          : [],
      expiresAtMillis: rec.expiresAtMillis,
    };
  }

  async confirmApproval(approvalId: string, authorization: ApprovalAuthorization): Promise<ConfirmResult> {
    this.assertOnline();
    await this.sleep(600);
    if (authorization.kind === 'red' && authorization.otp.length !== 6) {
      throw new ApprovalError('OTP_INVALID', '验证码不正确');
    }
    const rec = this.requireRecord(approvalId);
    if (rec.processed) throw new ApprovalError('APPROVAL_ALREADY_PROCESSED', '该审批已处理，请勿重复确认');
    if (rec.submitted) throw new ApprovalError('APPROVAL_ALREADY_SUBMITTED', '该审批已提交，请查询最新状态');
    if (this.now() > rec.expiresAtMillis) throw new ApprovalError('APPROVAL_EXPIRED', '预演已过期，请重新预演');

    switch (this.scenario.confirm) {
      case 'success': {
        rec.submitted = true;
        rec.processed = true;
        rec.finalStatus = 'success';
        this.applyOperation(rec.subscriptionId, rec);
        this.sync();
        return { status: 'success' };
      }
      case 'failedUncertain': {
        // 明确失败但状态不确定：中性失败说明，不声称「结果不明」也不声称「状态未变更」。
        rec.submitted = true;
        rec.processed = true;
        rec.finalStatus = 'failed';
        rec.finalReason = undefined;
        this.sync();
        return { status: 'failed' };
      }
      case 'failedNoChange': {
        // 明确失败且状态确定未变更。
        rec.submitted = true;
        rec.processed = true;
        rec.finalStatus = 'failed';
        rec.finalReason = '操作未完成，订阅状态未变更';
        this.sync();
        return { status: 'failed', reason: rec.finalReason };
      }
      case 'unknown': {
        rec.submitted = true;
        rec.processed = false;
        rec.finalStatus = null;
        rec.afterUnknownStatus = this.scenario.afterUnknownStatus;
        this.sync();
        return { status: 'unknown' };
      }
      default: {
        rec.submitted = true;
        rec.processed = true;
        rec.finalStatus = 'success';
        this.applyOperation(rec.subscriptionId, rec);
        this.sync();
        return { status: 'success' };
      }
    }
  }

  async getApprovalStatus(approvalId: string): Promise<ApprovalStatus> {
    this.assertOnline();
    await this.sleep();
    const rec = this.requireRecord(approvalId);
    if (rec.processed && rec.finalStatus) return rec.finalStatus;
    if (rec.submitted && !rec.processed) {
      const outcome = rec.afterUnknownStatus ?? 'unknown';
      if (outcome === 'success' || outcome === 'failed') {
        rec.finalStatus = outcome;
        rec.processed = true;
        if (outcome === 'success') this.applyOperation(rec.subscriptionId, rec);
        this.sync();
      }
      return outcome;
    }
    return 'unknown';
  }

  async rejectApproval(_approvalId: string): Promise<void> {
    this.assertOnline();
    await this.sleep();
  }
}
import type { ApprovalAuthorization, ApprovalStatus, ConfirmResult } from '../transfer/types';
import type { CardInfo, CardOperationKind, CardPlanView, CardPreviewResult } from './types';
import {
  ApprovalError,
  GatewayOfflineError,
  persistRecords,
  clearRecords,
  maxSeqFromIds,
  type PersistedRecord,
} from '../approval/shared';
import type { CardsService } from './CardsService';

export type MockCardConfirmOutcome = 'success' | 'failedUncertain' | 'failedNoChange' | 'unknown';
export type MockCardStatusOutcome = 'success' | 'failed' | 'unknown';

export interface MockCardsScenario {
  confirm: MockCardConfirmOutcome;
  afterUnknownStatus: MockCardStatusOutcome;
  planViewFailOnce: boolean;
  planViewExpired: boolean;
  offline: boolean;
  /** 返回空卡片列表。 */
  emptyList: boolean;
  /** 首次 listCards 失败一次。 */
  listFailOnce: boolean;
}

export const DEFAULT_CARDS_SCENARIO: MockCardsScenario = {
  confirm: 'success',
  afterUnknownStatus: 'unknown',
  planViewFailOnce: false,
  planViewExpired: false,
  offline: false,
  emptyList: false,
  listFailOnce: false,
};

const RECORDS_KEY = 'mock-cards-records';
const STATE_KEY = 'mock-cards-state';
const ID_PREFIX = 'mock-card-apv-';

interface CardRecord extends PersistedRecord {
  cardId: string;
  operation: CardOperationKind;
}

/** 基础卡片（明确 mock，尾号非真实卡号）。 */
const BASE_CARDS: CardInfo[] = [
  {
    id: 'c1',
    name: 'Everyday 借记卡',
    last4: '6094',
    maskedNumber: '•••• •••• •••• 6094',
    brand: 'debit',
    expiry: '08/29',
    expiringSoon: false,
    status: 'normal',
    onlinePayEnabled: true,
    availableOperations: ['lock', 'onlinePayOn', 'onlinePayOff', 'reportLost'],
  },
  {
    id: 'c2',
    name: '白金卡',
    last4: '8812',
    maskedNumber: '•••• •••• •••• 8812',
    brand: 'credit',
    expiry: '05/30',
    expiringSoon: false,
    status: 'locked',
    onlinePayEnabled: false,
    availableOperations: ['unlock', 'reportLost'],
  },
  {
    id: 'c3',
    name: '旅行卡',
    last4: '2205',
    maskedNumber: '•••• •••• •••• 2205',
    brand: 'credit',
    expiry: '11/26',
    expiringSoon: true,
    status: 'normal',
    onlinePayEnabled: true,
    availableOperations: ['lock', 'onlinePayOff', 'onlinePayOn', 'reportLost'],
  },
  {
    id: 'c4',
    name: '备用卡',
    last4: '4431',
    maskedNumber: '•••• •••• •••• 4431',
    brand: 'debit',
    expiry: '09/27',
    expiringSoon: false,
    status: 'reportedLost',
    onlinePayEnabled: false,
    availableOperations: [],
  },
];

type CardStateOverride = { status?: CardInfo['status']; onlinePayEnabled?: boolean };

function loadState(): Record<string, CardStateOverride> {
  try {
    const raw = window.sessionStorage.getItem(STATE_KEY);
    return raw ? (JSON.parse(raw) as Record<string, CardStateOverride>) : {};
  } catch {
    return {};
  }
}

function persistState(state: Record<string, CardStateOverride>): void {
  try {
    window.sessionStorage.setItem(STATE_KEY, JSON.stringify(state));
  } catch {
    /* ignore */
  }
}

export function clearMockCardsRecords(): void {
  clearRecords(RECORDS_KEY);
  try {
    window.sessionStorage.removeItem(STATE_KEY);
  } catch {
    /* ignore */
  }
}

function normalizeRecord(rec: Partial<CardRecord>): CardRecord {
  return {
    id: rec.id ?? '',
    expiresAtMillis: rec.expiresAtMillis ?? 0,
    submitted: rec.submitted ?? false,
    processed: rec.processed ?? false,
    finalStatus: rec.finalStatus ?? null,
    finalReason: rec.finalReason,
    noFundsMoved: rec.noFundsMoved,
    afterUnknownStatus: rec.afterUnknownStatus ?? null,
    cardId: rec.cardId ?? '',
    operation: rec.operation ?? 'lock',
  };
}

function loadCardRecords(): CardRecord[] {
  try {
    const raw = window.sessionStorage.getItem(RECORDS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.map((r) => normalizeRecord(r as Partial<CardRecord>)) : [];
  } catch {
    return [];
  }
}

/** 依据 service 权威规则决定操作风险等级。 */
function riskFor(operation: CardOperationKind): 'red' | 'yellow' {
  return operation === 'reportLost' ? 'red' : 'yellow';
}

function operationLabel(operation: CardOperationKind): string {
  switch (operation) {
    case 'lock':
      return '锁定卡片';
    case 'unlock':
      return '解锁卡片';
    case 'onlinePayOn':
      return '开启线上支付';
    case 'onlinePayOff':
      return '关闭线上支付';
    case 'reportLost':
      return '挂失卡片';
  }
}

export class MockCardsService implements CardsService {
  private scenario: MockCardsScenario;
  private delayMs: number;
  private now: () => number;
  private seq = 0;
  private planViewFailedOnce = false;
  private listFailedOnce = false;
  private records = new Map<string, CardRecord>();

  constructor(scenario?: Partial<MockCardsScenario>, delayMs = 300, now: () => number = () => Date.now()) {
    this.scenario = { ...DEFAULT_CARDS_SCENARIO, ...scenario };
    this.delayMs = delayMs;
    this.now = now;
    const recs = loadCardRecords();
    this.seq = maxSeqFromIds(recs, ID_PREFIX);
    for (const r of recs) this.records.set(r.id, r);
  }

  getScenario(): MockCardsScenario {
    return { ...this.scenario };
  }

  setScenario(patch: Partial<MockCardsScenario>): void {
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

  private baseCard(cardId: string): CardInfo | undefined {
    return BASE_CARDS.find((c) => c.id === cardId);
  }

  private applyOperation(cardId: string, operation: CardOperationKind): void {
    const state = loadState();
    const cur = state[cardId] ?? {};
    switch (operation) {
      case 'lock':
        cur.status = 'locked';
        cur.onlinePayEnabled = false;
        break;
      case 'unlock':
        cur.status = 'normal';
        break;
      case 'onlinePayOn':
        cur.onlinePayEnabled = true;
        break;
      case 'onlinePayOff':
        cur.onlinePayEnabled = false;
        break;
      case 'reportLost':
        cur.status = 'reportedLost';
        cur.onlinePayEnabled = false;
        break;
    }
    state[cardId] = cur;
    persistState(state);
  }

  private requireRecord(id: string): CardRecord {
    const rec = this.records.get(id);
    if (!rec) throw new ApprovalError('APPROVAL_NOT_FOUND', '审批不存在或已失效，请重新预演');
    return rec;
  }

  private computeCards(): CardInfo[] {
    const state = loadState();
    return BASE_CARDS.map((c) => {
      const override = state[c.id];
      const status = override?.status ?? c.status;
      const onlinePayEnabled = override?.onlinePayEnabled ?? c.onlinePayEnabled;
      const onlinePayOp: CardOperationKind = onlinePayEnabled ? 'onlinePayOff' : 'onlinePayOn';
      const ops: CardOperationKind[] =
        status === 'reportedLost'
          ? []
          : status === 'locked'
            ? ['unlock', 'reportLost']
            : ['lock', onlinePayOp, 'reportLost'];
      return { ...c, status, onlinePayEnabled, availableOperations: ops };
    });
  }

  async listCards(): Promise<CardInfo[]> {
    this.assertOnline();
    await this.sleep();
    if (this.scenario.listFailOnce && !this.listFailedOnce) {
      this.listFailedOnce = true;
      throw new Error('模拟列表加载失败');
    }
    if (this.scenario.emptyList) return [];
    return this.computeCards();
  }

  async prepareCardOperation(cardId: string, operation: CardOperationKind): Promise<CardPreviewResult> {
    this.assertOnline();
    await this.sleep();
    const current = this.computeCards().find((c) => c.id === cardId);
    if (!current) return { kind: 'error', code: 'NO_CARD', message: '卡片不存在' };
    if (!current.availableOperations.includes(operation)) {
      return { kind: 'error', code: 'OPERATION_UNAVAILABLE', message: '当前状态不可执行该操作' };
    }

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
      cardId,
      operation,
    });
    this.sync();
    return { kind: 'ok', approvalId };
  }

  async getPlanView(approvalId: string): Promise<CardPlanView> {
    this.assertOnline();
    await this.sleep();
    if (this.scenario.planViewFailOnce && !this.planViewFailedOnce) {
      this.planViewFailedOnce = true;
      throw new ApprovalError('PLANVIEW_LOAD_FAILED', '预演详情加载失败，请重试');
    }
    const rec = this.requireRecord(approvalId);
    const card = this.baseCard(rec.cardId);
    const riskLevel = riskFor(rec.operation);
    return {
      approvalId,
      riskLevel,
      title: operationLabel(rec.operation),
      cardName: card?.name ?? '卡片',
      maskedNumber: card?.maskedNumber ?? '',
      operationLabel: operationLabel(rec.operation),
      effectText:
        rec.operation === 'reportLost'
          ? '挂失后卡片立即停用，需要重新申领新卡，原卡不可继续使用。'
          : rec.operation === 'lock'
            ? '锁定后卡片暂停交易，可随时解锁恢复。'
            : rec.operation === 'unlock'
              ? '解锁后恢复卡片交易。'
              : rec.operation === 'onlinePayOn'
                ? '开启后可在支持渠道使用线上支付。'
                : '关闭后线上支付无法使用。',
      riskNotes: rec.operation === 'reportLost' ? ['挂失为高风险操作，请确认卡片确已丢失或被盗。'] : [],
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
        this.applyOperation(rec.cardId, rec.operation);
        this.sync();
        return { status: 'success' };
      }
      case 'failedUncertain': {
        // 明确失败但状态不确定：使用中性失败说明，不声称「结果不明」也不声称「状态未变更」。
        rec.submitted = true;
        rec.processed = true;
        rec.finalStatus = 'failed';
        rec.finalReason = undefined;
        rec.noFundsMoved = undefined;
        this.sync();
        return { status: 'failed' };
      }
      case 'failedNoChange': {
        // 明确失败且状态确定未变更（service 明确返回此结论）。
        rec.submitted = true;
        rec.processed = true;
        rec.finalStatus = 'failed';
        rec.finalReason = '操作未完成，卡片状态未变更';
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
        this.applyOperation(rec.cardId, rec.operation);
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
        if (outcome === 'success') this.applyOperation(rec.cardId, rec.operation);
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
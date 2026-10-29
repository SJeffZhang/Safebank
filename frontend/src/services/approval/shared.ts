import type { ApprovalStatus } from '../transfer/types';

/**
 * 卡片 / 订阅审批共享的错误与记录持久化，不依赖转账业务。
 * 两者与转账共用同一套 approve-preview → PlanView → confirm → status 语义。
 */

export class ApprovalError extends Error {
  code: string;
  constructor(code: string, message: string) {
    super(message);
    this.name = 'ApprovalError';
    this.code = code;
  }
}

/** 断网（只读/写操作统一抛此错误；离线保留已展示内容）。 */
export class GatewayOfflineError extends Error {
  constructor(message = '网络不可用') {
    super(message);
    this.name = 'GatewayOfflineError';
  }
}

/** 持久化的审批记录（仅恢复所需的最少信息，不存 OTP / 敏感信息）。 */
export interface PersistedRecord {
  id: string;
  expiresAtMillis: number;
  submitted: boolean;
  processed: boolean;
  finalStatus: ApprovalStatus | null;
  finalReason?: string;
  noFundsMoved?: boolean;
  afterUnknownStatus: ApprovalStatus | null;
}

function normalizeRecord(rec: Partial<PersistedRecord>): PersistedRecord {
  return {
    id: rec.id ?? '',
    expiresAtMillis: rec.expiresAtMillis ?? 0,
    submitted: rec.submitted ?? false,
    processed: rec.processed ?? false,
    finalStatus: rec.finalStatus ?? null,
    finalReason: rec.finalReason,
    noFundsMoved: rec.noFundsMoved,
    afterUnknownStatus: rec.afterUnknownStatus ?? null,
  };
}

export function loadRecords(key: string): PersistedRecord[] {
  try {
    const raw = window.sessionStorage.getItem(key);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.map((r) => normalizeRecord(r as Partial<PersistedRecord>));
  } catch {
    return [];
  }
}

export function persistRecords(key: string, records: PersistedRecord[]): void {
  try {
    window.sessionStorage.setItem(key, JSON.stringify(records));
  } catch {
    /* ignore */
  }
}

export function clearRecords(key: string): void {
  try {
    window.sessionStorage.removeItem(key);
  } catch {
    /* ignore */
  }
}

/** 从已有 ID 中恢复最大序号，避免刷新后新审批覆盖旧审批。 */
export function maxSeqFromIds(records: PersistedRecord[], prefix: string): number {
  let max = 0;
  const re = new RegExp(`^${prefix}(\\d+)$`);
  for (const r of records) {
    const m = re.exec(r.id);
    if (m) max = Math.max(max, Number(m[1]));
  }
  return max;
}
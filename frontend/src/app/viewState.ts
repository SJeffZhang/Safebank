/** 视图状态（非凭证）的 sessionStorage 读写，用于返回后保留分段 / 搜索词 / 筛选。 */

/** 理财「持仓 / 发现」分段。 */
export const WEALTH_TAB_KEY = 'sb-wealth-tab';
/** 理财持仓搜索词。 */
export const WEALTH_SEARCH_KEY = 'sb-wealth-search';
/** 全部持仓页「持仓 / 全部产品 / 比较」分段。 */
export const WEALTH_HOLDINGS_TAB_KEY = 'sb-wealth-holdings-tab';
/** 发现视图中展开的板块 id 列表。 */
export const WEALTH_SECTIONS_KEY = 'sb-wealth-sections';
/** 产品比较已选 productId 列表。 */
export const WEALTH_COMPARE_KEY = 'sb-wealth-compare';
/** 理财业务待确认审批的 approval_id（独立于转账/卡片/订阅）。 */
export const WEALTH_PENDING_APPROVAL_KEY = 'sb-wealth-pending-approval';
/** 待确认审批的 approval_id（仅存恢复所需的最少信息，不存 OTP、不存 PlanView）。 */
export const PENDING_APPROVAL_KEY = 'sb-pending-approval';
/** 卡片业务待确认审批的 approval_id（独立于转账/订阅）。 */
export const CARD_PENDING_APPROVAL_KEY = 'sb-card-pending-approval';
/** 订阅业务待确认审批的 approval_id（独立于转账/卡片）。 */
export const SUBSCRIPTION_PENDING_APPROVAL_KEY = 'sb-subscription-pending-approval';
/** 账单当前月份（YYYY-MM）。 */
export const BILL_MONTH_KEY = 'sb-bill-month';
/** 账单类别筛选。 */
export const BILL_CATEGORY_KEY = 'sb-bill-category';
/** 金额隐私偏好（localStorage 偏好，不属于业务视图状态）。 */
export const AMOUNT_HIDDEN_KEY = 'sb-amount-hidden';
/** 小安会话（对话、计划、澄清、输入草稿）。 */
export const ASSISTANT_VIEW_KEY = 'sb-assistant-view';
/** 个人中心展开的板块。 */
export const PROFILE_SECTIONS_KEY = 'sb-profile-sections';

/** 四类业务待确认审批的独立键（不合并，保持既有恢复逻辑）。 */
export const PENDING_APPROVAL_KEYS = {
  transfer: PENDING_APPROVAL_KEY,
  card: CARD_PENDING_APPROVAL_KEY,
  subscription: SUBSCRIPTION_PENDING_APPROVAL_KEY,
  wealth: WEALTH_PENDING_APPROVAL_KEY,
} as const;

export type PendingBusiness = keyof typeof PENDING_APPROVAL_KEYS;

/**
 * 登记在本阶段的客户业务视图状态键。
 * 退出登录时会逐个移除；用户偏好（主题、金额隐私、已访问标记）不在此列。
 */
export const BUSINESS_VIEW_KEYS = [
  WEALTH_TAB_KEY,
  WEALTH_SEARCH_KEY,
  WEALTH_HOLDINGS_TAB_KEY,
  WEALTH_SECTIONS_KEY,
  WEALTH_COMPARE_KEY,
  WEALTH_PENDING_APPROVAL_KEY,
  PENDING_APPROVAL_KEY,
  CARD_PENDING_APPROVAL_KEY,
  SUBSCRIPTION_PENDING_APPROVAL_KEY,
  BILL_MONTH_KEY,
  BILL_CATEGORY_KEY,
  ASSISTANT_VIEW_KEY,
  PROFILE_SECTIONS_KEY,
] as const;

export function readViewState<T>(key: string, fallback: T): T {
  try {
    const raw = window.sessionStorage.getItem(key);
    if (raw == null) return fallback;
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

export function writeViewState<T>(key: string, value: T): void {
  try {
    window.sessionStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* ignore */
  }
}

export function removeViewState(key: string): void {
  try {
    window.sessionStorage.removeItem(key);
  } catch {
    /* ignore */
  }
}

/** 精确清除已登记的业务视图状态（不做 clear()，避免误删偏好）。 */
export function clearBusinessViewState(): void {
  BUSINESS_VIEW_KEYS.forEach(removeViewState);
}

/** 保存待确认审批的 approval_id（仅这一项，不存 OTP / PlanView）。 */
export function savePendingApproval(approvalId: string): void {
  writeViewState(PENDING_APPROVAL_KEY, approvalId);
}

export function readPendingApproval(): string | null {
  const value = readViewState<string | null>(PENDING_APPROVAL_KEY, null);
  if (typeof value === 'string' && value.length > 0) return value;
  return null;
}

export function clearPendingApproval(): void {
  removeViewState(PENDING_APPROVAL_KEY);
}

/** 通用 pending approval 读写，供卡片/订阅等业务复用（不存 OTP / 敏感信息）。 */
export function makePendingAccessors(key: string) {
  return {
    read(): string | null {
      const value = readViewState<string | null>(key, null);
      return typeof value === 'string' && value.length > 0 ? value : null;
    },
    save(id: string): void {
      writeViewState(key, id);
    },
    clear(): void {
      removeViewState(key);
    },
  };
}

/** 金额隐私偏好：属于用户偏好，退出登录不清除。 */
export function readAmountHidden(): boolean {
  try {
    return window.localStorage.getItem(AMOUNT_HIDDEN_KEY) === '1';
  } catch {
    return false;
  }
}

export function writeAmountHidden(hidden: boolean): void {
  try {
    if (hidden) window.localStorage.setItem(AMOUNT_HIDDEN_KEY, '1');
    else window.localStorage.removeItem(AMOUNT_HIDDEN_KEY);
  } catch {
    /* ignore */
  }
}
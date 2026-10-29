/**
 * 金额工具：全程使用整数最小货币单位「分」(cents)，禁止浮点计算资金。
 */

export const MAX_SAFE_CENTS = Number.MAX_SAFE_INTEGER;
/** 单笔金额上限（分）之上视为不安全，避免乘法溢出两位小数拼接。 */
const MAX_YUAN = Math.floor(MAX_SAFE_CENTS / 100);

export type ParseResult = { cents: number } | { error: string };

/**
 * 把用户输入解析为整数分。
 * 接受：纯数字（可带 1~2 位小数）；拒绝：负数、指数、超过两位小数、非法字符、非安全整数、0 或更小。
 */
export function parseAmountToCents(text: string): ParseResult {
  const trimmed = text.trim();
  if (trimmed === '') return { error: '请输入金额' };

  if (!/^\d+(\.\d{0,2})?$/.test(trimmed)) {
    return { error: '金额格式不正确：仅支持数字与最多两位小数' };
  }

  const [intPart, fracPart = ''] = trimmed.split('.');
  const yuan = Number(intPart);
  if (!Number.isSafeInteger(yuan)) return { error: '金额过大' };
  if (yuan > MAX_YUAN) return { error: '金额过大' };

  const fen = Number(fracPart.padEnd(2, '0') || '0');
  const cents = yuan * 100 + fen;

  if (!Number.isSafeInteger(cents)) return { error: '金额过大' };
  if (cents <= 0) return { error: '金额需大于 0' };

  return { cents };
}

/** 整数分相加（纯整数，无浮点误差）。 */
export function addCents(a: number, b: number): number {
  return a + b;
}

/** 整数分相减（纯整数，无浮点误差）。 */
export function subCents(a: number, b: number): number {
  return a - b;
}

function groupThousands(intPart: string): string {
  return intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}

/**
 * 把整数分格式化为「1,234.56」样式的字符串。
 * 全程整数运算，不用浮点 `toFixed`，避免精度问题。
 */
export function formatCents(cents: number): string {
  const sign = cents < 0 ? '-' : '';
  const abs = Math.abs(Math.trunc(cents));
  const yuan = Math.floor(abs / 100).toString();
  const fen = (abs % 100).toString().padStart(2, '0');
  return `${sign}${groupThousands(yuan)}.${fen}`;
}

/** 带货币符号、供展示用的金额字符串。 */
export function formatYuan(cents: number): string {
  return `¥${formatCents(cents)}`;
}
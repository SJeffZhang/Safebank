import { formatCents } from '../../components/AmountText';

/** 金额：隐藏时掩码；金额一律整数分。 */
export function formatAmount(cents: number, hidden: boolean): string {
  return hidden ? '••••' : formatCents(cents, false);
}

/** 金额（带正负号）。 */
export function formatAmountSigned(cents: number, hidden: boolean): string {
  return hidden ? '••••' : formatCents(cents, true);
}

/** 比例 → 百分比，保留 2 位；带正负号。 */
export function formatPercent(ratio: number): string {
  const pct = ratio * 100;
  const sign = pct > 0 ? '+' : pct < 0 ? '−' : '';
  return `${sign}${Math.abs(pct).toFixed(2)}%`;
}

/** 份额（非金额）。 */
export function formatShares(shares: number): string {
  return shares.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/** 单位净值（非金额）。 */
export function formatNetValue(value: number): string {
  return value.toFixed(4);
}

/** 元输入 → 整数分；非法返回 error。 */
export function parseYuanToCents(text: string): { cents: number } | { error: string } {
  const raw = text.trim();
  if (raw === '') return { error: '请输入金额' };
  if (!/^\d+(\.\d{1,2})?$/.test(raw)) return { error: '金额格式不正确，最多两位小数' };
  const [intPart, decPart = ''] = raw.split('.');
  const cents = Number(intPart) * 100 + Number(decPart.padEnd(2, '0'));
  if (!Number.isSafeInteger(cents)) return { error: '金额超出可处理范围' };
  if (cents <= 0) return { error: '金额必须大于 0' };
  return { cents };
}

/** 份额输入校验。 */
export function parseShares(text: string): { shares: number } | { error: string } {
  const raw = text.trim();
  if (raw === '') return { error: '请输入份额' };
  if (!/^\d+(\.\d{1,2})?$/.test(raw)) return { error: '份额格式不正确，最多两位小数' };
  const value = Number(raw);
  if (!Number.isFinite(value) || value <= 0) return { error: '份额必须大于 0' };
  return { shares: Math.round(value * 100) / 100 };
}
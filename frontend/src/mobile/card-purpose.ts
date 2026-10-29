/** Personal display labels only. Never changes banking permissions or L1 input. */
export const purposeOptions = ['日常消费', '工资收款', '生活缴费', '出行备用', '储蓄备用', '投资理财'];
export const previewPurposes: Record<string, string> = {
  crd_zs_1: '日常消费', crd_zs_2: '出行备用', crd_zs_3: '生活缴费',
};
export const purposeStorageKey = (customerId: string) => `safebank:card-purpose:v1:${customerId}`;
export function loadPurposes(customerId: string): Record<string, string> {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(purposeStorageKey(customerId)) ?? '{}');
    if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
    return Object.fromEntries(Object.entries(value).filter(([, label]) =>
      typeof label === 'string' && label.trim().length > 0 && label.length <= 12));
  } catch { return {}; }
}

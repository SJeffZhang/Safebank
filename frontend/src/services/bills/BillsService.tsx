import { createContext, useContext, useMemo } from 'react';
import type { ReactNode } from 'react';
import type { MonthlyBill } from './types';
import { MockBillsService } from './MockBillsService';

/** 可替换的账单/异常服务。后续用 HTTP adapter 替换 MockBillsService 即可，页面无需改动。 */
export interface BillsService {
  /** 返回可切换的月份列表（YYYY-MM，desc）。 */
  getAvailableMonths(): Promise<string[]>;
  /** 返回某月汇总、分类、日趋势、流水与异常证据。 */
  getMonthlyBill(month: string): Promise<MonthlyBill>;
}

/** 断网类错误（读-only：离线时保留已展示内容）。 */
export class BillsOfflineError extends Error {
  constructor(message = '网络不可用') {
    super(message);
    this.name = 'BillsOfflineError';
  }
}

const BillsServiceContext = createContext<BillsService | null>(null);

export function BillsServiceProvider({
  children,
  service,
}: {
  children: ReactNode;
  service?: BillsService;
}) {
  const value = useMemo(() => service ?? new MockBillsService(), [service]);
  return <BillsServiceContext.Provider value={value}>{children}</BillsServiceContext.Provider>;
}

export function useBillsService(): BillsService {
  const service = useContext(BillsServiceContext);
  if (!service) throw new Error('useBillsService must be used inside BillsServiceProvider');
  return service;
}
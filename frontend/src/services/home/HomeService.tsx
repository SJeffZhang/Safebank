import { createContext, useContext, useMemo } from 'react';
import type { ReactNode } from 'react';
import type { HomeBill, HomeSummary, HomeTransaction, HomeUpcoming } from './types';
import { MockHomeService } from './MockHomeService';
import { useAuthService } from '../auth/AuthService';
import { useTransferService } from '../transfer/TransferService';
import { useWealthService } from '../wealth/WealthService';
import { useSubscriptionsService } from '../subscriptions/SubscriptionsService';
import { useBillsService } from '../bills/BillsService';

/** 首页聚合服务：只组合各业务 service，不复制业务数组。 */
export interface HomeService {
  getSummary(customerId: string): Promise<HomeSummary>;
  getUpcoming(): Promise<HomeUpcoming>;
  getRecentTransactions(): Promise<HomeTransaction[]>;
  getMonthlyBill(): Promise<HomeBill>;
}

const HomeServiceContext = createContext<HomeService | null>(null);

export function HomeServiceProvider({ children, service }: { children: ReactNode; service?: HomeService }) {
  if (service) {
    return <HomeServiceContext.Provider value={service}>{children}</HomeServiceContext.Provider>;
  }
  return <ComposedHomeProvider>{children}</ComposedHomeProvider>;
}

/** 组合各业务 service 的默认实现（仅在未注入 service 时使用）。 */
function ComposedHomeProvider({ children }: { children: ReactNode }) {
  const auth = useAuthService();
  const transfer = useTransferService();
  const wealth = useWealthService();
  const subscriptions = useSubscriptionsService();
  const bills = useBillsService();
  const value = useMemo(
    () => new MockHomeService({ auth, transfer, wealth, subscriptions, bills }),
    [auth, transfer, wealth, subscriptions, bills],
  );
  return <HomeServiceContext.Provider value={value}>{children}</HomeServiceContext.Provider>;
}

export function useHomeService(): HomeService {
  const service = useContext(HomeServiceContext);
  if (!service) throw new Error('useHomeService must be used inside HomeServiceProvider');
  return service;
}
import { createContext, useContext, useMemo } from 'react';
import type { ReactNode } from 'react';
import type { ApprovalAuthorization, ApprovalStatus, ConfirmResult } from '../transfer/types';
import type {
  TrendPeriod,
  WealthHolding,
  WealthOverview,
  WealthPlanView,
  WealthPreviewResult,
  WealthProduct,
} from './types';
import { MockWealthService } from './MockWealthService';

/** 独立的理财事实数据 + 审批服务。页面不直接引用 mock 数组。 */
export interface WealthService {
  getOverview(period: TrendPeriod): Promise<WealthOverview>;
  getHoldings(): Promise<WealthHolding[]>;
  getProducts(): Promise<WealthProduct[]>;
  preparePurchase(productId: string, amountCents: number): Promise<WealthPreviewResult>;
  prepareRedemption(holdingId: string, shares: number): Promise<WealthPreviewResult>;
  getPlanView(approvalId: string): Promise<WealthPlanView>;
  confirmApproval(approvalId: string, authorization: ApprovalAuthorization): Promise<ConfirmResult>;
  getApprovalStatus(approvalId: string): Promise<ApprovalStatus>;
  rejectApproval(approvalId: string): Promise<void>;
}

const WealthServiceContext = createContext<WealthService | null>(null);

export function WealthServiceProvider({ children, service }: { children: ReactNode; service?: WealthService }) {
  const value = useMemo(() => service ?? new MockWealthService(), [service]);
  return <WealthServiceContext.Provider value={value}>{children}</WealthServiceContext.Provider>;
}

export function useWealthService(): WealthService {
  const service = useContext(WealthServiceContext);
  if (!service) throw new Error('useWealthService must be used inside WealthServiceProvider');
  return service;
}
import { createContext, useContext, useMemo } from 'react';
import type { ReactNode } from 'react';
import type { ApprovalAuthorization, ConfirmResult, ApprovalStatus } from '../transfer/types';
import type {
  Subscription,
  SubscriptionPlanView,
  SubscriptionPreviewResult,
  SubscriptionSummary,
  UpcomingCharges,
} from './types';
import { MockSubscriptionsService } from './MockSubscriptionsService';

export interface SubscriptionsService {
  listSubscriptions(): Promise<Subscription[]>;
  getUpcomingCharges(): Promise<UpcomingCharges>;
  getSummary(): Promise<SubscriptionSummary>;
  prepareCancellation(subscriptionId: string): Promise<SubscriptionPreviewResult>;
  prepareReminderChange(subscriptionId: string, reminderTime: string): Promise<SubscriptionPreviewResult>;
  getPlanView(approvalId: string): Promise<SubscriptionPlanView>;
  confirmApproval(approvalId: string, authorization: ApprovalAuthorization): Promise<ConfirmResult>;
  getApprovalStatus(approvalId: string): Promise<ApprovalStatus>;
  rejectApproval(approvalId: string): Promise<void>;
}

const SubscriptionsServiceContext = createContext<SubscriptionsService | null>(null);

export function SubscriptionsServiceProvider({
  children,
  service,
}: {
  children: ReactNode;
  service?: SubscriptionsService;
}) {
  const value = useMemo(() => service ?? new MockSubscriptionsService(), [service]);
  return <SubscriptionsServiceContext.Provider value={value}>{children}</SubscriptionsServiceContext.Provider>;
}

export function useSubscriptionsService(): SubscriptionsService {
  const service = useContext(SubscriptionsServiceContext);
  if (!service) throw new Error('useSubscriptionsService must be used inside SubscriptionsServiceProvider');
  return service;
}
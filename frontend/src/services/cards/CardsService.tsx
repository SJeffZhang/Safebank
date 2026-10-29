import { createContext, useContext, useMemo } from 'react';
import type { ReactNode } from 'react';
import type { ApprovalAuthorization, ConfirmResult, ApprovalStatus } from '../transfer/types';
import type { CardInfo, CardPlanView, CardOperationKind, CardPreviewResult } from './types';
import { MockCardsService } from './MockCardsService';

export interface CardsService {
  listCards(): Promise<CardInfo[]>;
  /** 预演一个卡片写操作，返回 approval_id。 */
  prepareCardOperation(cardId: string, operation: CardOperationKind): Promise<CardPreviewResult>;
  getPlanView(approvalId: string): Promise<CardPlanView>;
  confirmApproval(approvalId: string, authorization: ApprovalAuthorization): Promise<ConfirmResult>;
  getApprovalStatus(approvalId: string): Promise<ApprovalStatus>;
  rejectApproval(approvalId: string): Promise<void>;
}

const CardsServiceContext = createContext<CardsService | null>(null);

export function CardsServiceProvider({ children, service }: { children: ReactNode; service?: CardsService }) {
  const value = useMemo(() => service ?? new MockCardsService(), [service]);
  return <CardsServiceContext.Provider value={value}>{children}</CardsServiceContext.Provider>;
}

export function useCardsService(): CardsService {
  const service = useContext(CardsServiceContext);
  if (!service) throw new Error('useCardsService must be used inside CardsServiceProvider');
  return service;
}
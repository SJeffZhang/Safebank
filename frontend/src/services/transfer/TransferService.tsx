import { createContext, useContext, useMemo } from 'react';
import type { ReactNode } from 'react';
import type {
  ApprovalAuthorization,
  ApprovalStatus,
  ConfirmResult,
  PlanView,
  PreviewResult,
  TransferAccount,
  TransferInput,
  TransferRecipient,
} from './types';
import { MockTransferService } from './MockTransferService';

/** 可替换的转账/审批服务接口。后续用 HTTP adapter 替换 MockTransferService 即可。 */
export interface TransferService {
  listAccounts(): Promise<TransferAccount[]>;
  listRecipients(): Promise<TransferRecipient[]>;
  getDailyRemainingCents(): Promise<number>;
  previewTransfer(input: TransferInput): Promise<PreviewResult>;
  getPlanView(approvalId: string): Promise<PlanView>;
  confirmApproval(approvalId: string, authorization: ApprovalAuthorization): Promise<ConfirmResult>;
  getApprovalStatus(approvalId: string): Promise<ApprovalStatus>;
  rejectApproval(approvalId: string): Promise<void>;
}

/** 断网/超时等网络类错误（结果不明确时应进入 UNKNOWN 或保持现有内容）。 */
export class OfflineError extends Error {
  constructor(message = '网络不可用') {
    super(message);
    this.name = 'OfflineError';
  }
}

/** 预演/校验类的业务错误（不会进入 UNKNOWN）。 */
export class TransferError extends Error {
  readonly code: string;
  constructor(code: string, message: string) {
    super(message);
    this.name = 'TransferError';
    this.code = code;
  }
}

const TransferServiceContext = createContext<TransferService | null>(null);

export function TransferServiceProvider({
  children,
  service,
}: {
  children: ReactNode;
  service?: TransferService;
}) {
  const value = useMemo(() => service ?? new MockTransferService(), [service]);
  return <TransferServiceContext.Provider value={value}>{children}</TransferServiceContext.Provider>;
}

export function useTransferService(): TransferService {
  const service = useContext(TransferServiceContext);
  if (!service) throw new Error('useTransferService must be used inside TransferServiceProvider');
  return service;
}
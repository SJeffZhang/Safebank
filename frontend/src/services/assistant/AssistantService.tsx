import { createContext, useContext, useMemo } from 'react';
import type { ReactNode } from 'react';
import type { AssistantHint, AssistantReply, InstructionInput } from './types';
import { MockAssistantService } from './MockAssistantService';

/** 小安服务：只理解、查询、规划、解释；不执行任何写操作。 */
export interface AssistantService {
  getHints(): Promise<AssistantHint[]>;
  submitInstruction(input: InstructionInput): Promise<AssistantReply>;
  selectCandidate(clarificationId: string, candidateId: string): Promise<AssistantReply>;
  submitClarificationAnswer(clarificationId: string, answer: string): Promise<AssistantReply>;
  /** 澄清过期后重新发起（使用原指令，产生新的 clarification_id）。 */
  restartClarification(clarificationId: string): Promise<AssistantReply>;
  cancelPlan(planId: string): Promise<void>;
}

export class AssistantOfflineError extends Error {
  constructor(message = '网络不可用') {
    super(message);
    this.name = 'AssistantOfflineError';
  }
}

/** 生成/澄清类的业务错误（保留输入，可就地提示）。 */
export class AssistantError extends Error {
  readonly code: string;
  constructor(code: string, message: string) {
    super(message);
    this.name = 'AssistantError';
    this.code = code;
  }
}

const AssistantServiceContext = createContext<AssistantService | null>(null);

export function AssistantServiceProvider({ children, service }: { children: ReactNode; service?: AssistantService }) {
  const value = useMemo(() => service ?? new MockAssistantService(), [service]);
  return <AssistantServiceContext.Provider value={value}>{children}</AssistantServiceContext.Provider>;
}

export function useAssistantService(): AssistantService {
  const service = useContext(AssistantServiceContext);
  if (!service) throw new Error('useAssistantService must be used inside AssistantServiceProvider');
  return service;
}
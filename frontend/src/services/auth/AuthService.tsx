import { createContext, useContext, useMemo } from 'react';
import type { ReactNode } from 'react';
import type { AuthResult, DemoCustomer, LockState } from './types';
import { MockAuthService } from './MockAuthService';

/** 演示认证服务：客户目录 + 权威 PIN 校验、错误计数与锁定状态。 */
export interface AuthService {
  getCustomers(): Promise<DemoCustomer[]>;
  /** 读取权威锁定状态（刷新、切换客户都不能绕过）。 */
  getLockState(customerId: string): Promise<LockState>;
  submitPin(customerId: string, pin: string): Promise<AuthResult>;
}

const AuthServiceContext = createContext<AuthService | null>(null);

export function AuthServiceProvider({ children, service }: { children: ReactNode; service?: AuthService }) {
  const value = useMemo(() => service ?? new MockAuthService(), [service]);
  return <AuthServiceContext.Provider value={value}>{children}</AuthServiceContext.Provider>;
}

export function useAuthService(): AuthService {
  const service = useContext(AuthServiceContext);
  if (!service) throw new Error('useAuthService must be used inside AuthServiceProvider');
  return service;
}
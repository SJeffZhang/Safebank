import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import {
  clearSession,
  DEMO_SESSION,
  hasVisited,
  markVisited,
  readSession,
  writeSession,
} from '../session/session';
import type { SessionStatus } from '../session/session';
import { clearBusinessViewState } from './viewState';
import { clearScrollMemory } from './scrollMemory';
import { clearMockTransferRecords } from '../services/transfer/MockTransferService';
import { clearMockCardsRecords } from '../services/cards/MockCardsService';
import { clearMockSubscriptionsRecords } from '../services/subscriptions/MockSubscriptionsService';
import { clearMockWealthRecords } from '../services/wealth/MockWealthService';

export interface ActiveCustomer {
  customerId: string;
  customerName: string;
}

interface SessionContextValue {
  status: SessionStatus;
  /** 当前登录客户（匿名时为演示占位）。 */
  customer: ActiveCustomer;
  /** 演示登录：建立会话并置为 authenticated。PIN 校验由 AuthService 完成，这里只落会话。 */
  login: (customer?: ActiveCustomer) => void;
  /** 退出：清除演示会话、业务视图状态与滚动记忆，保留主题 / 金额隐私 / 已访问标记 / 认证锁定计数。 */
  logout: () => void;
  /** 标记“已访问”，决定下次是否再显示欢迎页。 */
  markAppVisited: () => void;
  hasVisitedApp: boolean;
}

const SessionContext = createContext<SessionContextValue | null>(null);

const FALLBACK_CUSTOMER: ActiveCustomer = {
  customerId: DEMO_SESSION.customerId ?? 'demo',
  customerName: DEMO_SESSION.customerName ?? '演示客户',
};

export interface SessionProviderProps {
  children: ReactNode;
  /** 测试注入：直接指定已确定的状态，跳过 resolving 计时器。 */
  initialStatus?: SessionStatus;
  /** resolving 持续时长（ms），默认 0；测试可放大以观察加载态。 */
  resolveDelayMs?: number;
}

export function SessionProvider({ children, initialStatus, resolveDelayMs = 0 }: SessionProviderProps) {
  const [status, setStatus] = useState<SessionStatus>(initialStatus ?? 'resolving');
  const [visited, setVisited] = useState<boolean>(hasVisited);
  const [customer, setCustomer] = useState<ActiveCustomer>(() => {
    const stored = readSession();
    if (stored?.customerId) {
      return { customerId: stored.customerId, customerName: stored.customerName ?? stored.customerId };
    }
    return FALLBACK_CUSTOMER;
  });

  useEffect(() => {
    if (initialStatus) return;
    const timer = window.setTimeout(() => {
      setStatus(readSession() ? 'authenticated' : 'anonymous');
    }, resolveDelayMs);
    return () => window.clearTimeout(timer);
  }, [initialStatus, resolveDelayMs]);

  const login = useCallback((next?: ActiveCustomer) => {
    const payload: ActiveCustomer = next ?? FALLBACK_CUSTOMER;
    writeSession({ authenticated: true, customerId: payload.customerId, customerName: payload.customerName });
    setCustomer(payload);
    setStatus('authenticated');
  }, []);

  const logout = useCallback(() => {
    clearSession();
    clearBusinessViewState();
    clearScrollMemory();
    clearMockTransferRecords();
    clearMockCardsRecords();
    clearMockSubscriptionsRecords();
    clearMockWealthRecords();
    // 认证错误计数与锁定属于安全状态，故意保留，避免通过退出重登绕过锁定。
    setCustomer(FALLBACK_CUSTOMER);
    setStatus('anonymous');
  }, []);

  const markAppVisited = useCallback(() => {
    markVisited();
    setVisited(true);
  }, []);

  const value = useMemo<SessionContextValue>(
    () => ({ status, customer, login, logout, markAppVisited, hasVisitedApp: visited }),
    [status, customer, login, logout, markAppVisited, visited],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionContextValue {
  const value = useContext(SessionContext);
  if (!value) throw new Error('useSession must be used inside SessionProvider');
  return value;
}
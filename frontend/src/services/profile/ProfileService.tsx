import { createContext, useContext, useMemo } from 'react';
import type { ReactNode } from 'react';
import type { AuditEntry, NotificationItem, ProfileOverview } from './types';
import { MockProfileService } from './MockProfileService';
import { GatewayOfflineError } from '../approval/shared';
import { useWealthService } from '../wealth/WealthService';
import { useAuthService } from '../auth/AuthService';
import { useHomeService } from '../home/HomeService';

/** 个人中心只读服务：账户摘要、审计链、通知历史。不提供任何写操作。 */
export interface ProfileService {
  /** customerId 用于读取当前登录客户的权威身份。 */
  getOverview(customerId?: string): Promise<ProfileOverview>;
  getAudit(): Promise<AuditEntry[]>;
  getNotifications(): Promise<NotificationItem[]>;
}

const ProfileServiceContext = createContext<ProfileService | null>(null);

export function ProfileServiceProvider({ children, service }: { children: ReactNode; service?: ProfileService }) {
  // 显式注入 service 时不需要任何业务 provider，便于测试与替换。
  if (service) {
    return <ProfileServiceContext.Provider value={service}>{children}</ProfileServiceContext.Provider>;
  }
  return <ComposedProfileProvider>{children}</ComposedProfileProvider>;
}

/** 默认实现：复合理财、认证与首页 service，资产口径统一由 HomeService.getSummary 提供。 */
function ComposedProfileProvider({ children }: { children: ReactNode }) {
  const wealth = useWealthService();
  const auth = useAuthService();
  const home = useHomeService();
  const value = useMemo(() => new MockProfileService(wealth, {}, 300, auth, home), [wealth, auth, home]);
  return <ProfileServiceContext.Provider value={value}>{children}</ProfileServiceContext.Provider>;
}

export function useProfileService(): ProfileService {
  const service = useContext(ProfileServiceContext);
  if (!service) throw new Error('useProfileService must be used inside ProfileServiceProvider');
  return service;
}

export { GatewayOfflineError };
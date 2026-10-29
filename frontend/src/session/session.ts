/**
 * 演示会话适配层（可替换，不伪装真实认证）。
 *
 * 会话状态：
 * - resolving   正在恢复会话（不应闪现任何具象页面）
 * - anonymous   无有效会话
 * - authenticated 存在有效的演示会话
 *
 * localStorage 键：
 * - sb-has-visited  偏好标记，决定是否再显示欢迎页，不是登录凭证
 * - sb-demo-session 演示会话（仅标记，不含 PIN / 客户业务数据）
 *
 * 后续接入真实认证时，替换本文件里的 read/write 实现即可，路由与页面无需改动。
 */

export type SessionStatus = 'resolving' | 'anonymous' | 'authenticated';

export interface DemoSession {
  authenticated: boolean;
  customerId?: string;
  customerName?: string;
}

const KEYS = {
  visited: 'sb-has-visited',
  session: 'sb-demo-session',
} as const;

export function hasVisited(): boolean {
  try {
    return window.localStorage.getItem(KEYS.visited) === '1';
  } catch {
    return false;
  }
}

export function markVisited(): void {
  try {
    window.localStorage.setItem(KEYS.visited, '1');
  } catch {
    /* ignore */
  }
}

export function readSession(): DemoSession | null {
  try {
    const raw = window.localStorage.getItem(KEYS.session);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as DemoSession;
    return parsed.authenticated ? parsed : null;
  } catch {
    return null;
  }
}

export function writeSession(session: DemoSession): void {
  try {
    window.localStorage.setItem(KEYS.session, JSON.stringify(session));
  } catch {
    /* ignore */
  }
}

export function clearSession(): void {
  try {
    window.localStorage.removeItem(KEYS.session);
  } catch {
    /* ignore */
  }
}

export const DEMO_SESSION: DemoSession = {
  authenticated: true,
  customerId: 'demo',
  customerName: '演示客户',
};
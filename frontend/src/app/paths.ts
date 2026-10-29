/** 路由路径常量，用于统一引用与测试。 */

export const PATHS = {
  welcome: '/welcome',
  login: '/login',
  home: '/',
  assistant: '/assistant',
  profile: '/profile',
  transfer: '/transfer',
  bills: '/bills',
  cards: '/cards',
  subscriptions: '/subscriptions',
  wealth: '/wealth',
  wealthHoldings: '/wealth/holdings',
  devComponents: '/dev/components',
} as const;

/** 二级页深链接无来源时的备用返回目标。 */
export const SECONDARY_FALLBACKS: Record<string, string> = {
  [PATHS.transfer]: PATHS.home,
  [PATHS.bills]: PATHS.home,
  [PATHS.cards]: PATHS.home,
  [PATHS.subscriptions]: PATHS.profile,
  [PATHS.wealth]: PATHS.home,
  [PATHS.wealthHoldings]: PATHS.wealth,
};

export function secondaryFallbackFor(pathname: string): string {
  return SECONDARY_FALLBACKS[pathname] ?? PATHS.home;
}

/** 登录成功后允许返回的目标：只接受应用内已登记的路径，非法目标回首页。 */
export function safeReturnPath(candidate: unknown): string {
  if (typeof candidate !== 'string') return PATHS.home;
  const known = Object.values(PATHS) as string[];
  return known.includes(candidate) ? candidate : PATHS.home;
}
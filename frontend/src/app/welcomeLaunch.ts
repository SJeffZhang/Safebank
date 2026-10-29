/**
 * 欢迎页启动守卫的“本次文档”状态。
 *
 * 使用模块级变量而不是 localStorage / sessionStorage：
 * - 新开页面、关闭后重开、F5 刷新都会重新加载 JS，因此重新显示欢迎页；
 * - SPA 内路由跳转与前进后退不会重新加载模块，因此不会重复打断用户。
 */
let welcomeSeenThisDocument = false;

export function hasSeenWelcomeThisDocument(): boolean {
  return welcomeSeenThisDocument;
}

export function markWelcomeSeenThisDocument(): void {
  welcomeSeenThisDocument = true;
}

/** 仅用于测试：模拟一次新的整页加载。 */
export function resetWelcomeLaunchForTests(): void {
  welcomeSeenThisDocument = false;
}

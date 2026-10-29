/**
 * 窗口滚动的最小封装，便于在测试中 mock 而无需依赖 jsdom 的 scrollTo 实现。
 */
export function applyScroll(top: number): void {
  window.scrollTo(0, top);
}

export function readScrollTop(): number {
  return typeof window.scrollY === 'number' ? window.scrollY : 0;
}
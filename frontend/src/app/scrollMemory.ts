/**
 * 极简视图滚动位置记忆（进程内）。
 * - 每个路径保存其滚动位置，用于“返回来源页 / Tab 切换”恢复。
 * - 首次访问某路径从顶部开始。
 * - 不持久化会话凭证；事实数据（余额、审批结果）仍需重新获取。
 */

const positions = new Map<string, number>();
const visited = new Set<string>();

export function saveScroll(path: string, top: number): void {
  positions.set(path, Math.max(0, top));
  visited.add(path);
}

export function getScroll(path: string): number {
  return positions.get(path) ?? 0;
}

export function isVisitedPath(path: string): boolean {
  return visited.has(path);
}

export function markVisitedPath(path: string): void {
  visited.add(path);
}

/**
 * 清除进程内滚动记忆。
 * 用于：退出登录（避免下一位演示用户继承上一位的浏览位置）、测试隔离。
 */
export function clearScrollMemory(): void {
  positions.clear();
  visited.clear();
}
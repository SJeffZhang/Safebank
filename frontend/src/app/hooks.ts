import { useCallback, useEffect, useLayoutEffect, useRef } from 'react';
import { useLocation, useNavigate, useNavigationType } from 'react-router-dom';
import { applyScroll, readScrollTop } from './scroll';
import { getScroll, isVisitedPath, saveScroll } from './scrollMemory';

/**
 * 在指定路由路径上恢复/记录窗口滚动位置。
 * - 每次 path 变化都重新判断：未访问 → 滚到 0；已访问 → 恢复到该路径保存的位置。
 * - 离开旧路径时（effect cleanup）先保存旧路径位置，再恢复新路径位置。
 * 各路径独立记忆，互不串用。
 *
 * 注意：cleanup 跑在 React 提交新页面 DOM 之后，此刻旧页面已被卸载、浏览器可能已把
 * scrollY 夹到 0；因此用 scroll 监听事件维护的 ref 记录“离开前”的位置，而不是在
 * cleanup 里直接读 window.scrollY。
 */
export function useScrollRestore(path: string): void {
  const scrollRef = useRef(0);

  useEffect(() => {
    const onScroll = () => {
      scrollRef.current = readScrollTop();
    };
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  useLayoutEffect(() => {
    const top = isVisitedPath(path) ? getScroll(path) : 0;
    applyScroll(top);
    return () => {
      saveScroll(path, scrollRef.current);
    };
  }, [path]);
}

/**
 * 返回按钮行为：有来源历史时后退一步，深链接直入（无来源）时回到备用路径。
 */
export function useBack(fallback: string): () => void {
  const navigate = useNavigate();
  const location = useLocation();

  return useCallback(() => {
    if (location.key === 'default') {
      navigate(fallback, { replace: true });
    } else {
      navigate(-1);
    }
  }, [location.key, fallback, navigate]);
}

export function useDocumentTitle(title: string): void {
  useEffect(() => {
    document.title = title;
  }, [title]);
}

/**
 * 推送(PUSH)进入新页时，把焦点移到主内容区；返回(POP)或 Tab 切换(REPLACE)不抢夺焦点。
 */
export function useFocusMainOnPush(): void {
  const location = useLocation();
  const navigationType = useNavigationType();

  useEffect(() => {
    if (navigationType !== 'PUSH') return;
    const main = document.getElementById('main-content');
    main?.focus();
  }, [location.pathname, navigationType]);
}
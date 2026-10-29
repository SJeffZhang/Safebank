import { useEffect } from 'react';

/**
 * 指针跟随高光（sheen）的全局驱动。
 *
 * 只在「有指针 + 支持悬停」的设备上注册一个 document 级 pointermove，
 * 用 requestAnimationFrame 合并成每帧最多一次写入，避免每个卡片各自挂监听。
 * 目标元素只需带 `data-sb-sheen` 属性，样式见 styles/interactions.css。
 *
 * 触屏设备、jsdom、以及不支持 matchMedia 的环境会直接跳过——不注册任何监听，
 * 因此移动端不会出现"手指移开后高光卡在原地"的悬停残留。
 */
export function useSheenPointer(): void {
  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return;
    if (!window.matchMedia('(hover: hover) and (pointer: fine)').matches) return;

    let frame = 0;
    let pending: { el: HTMLElement; x: number; y: number } | null = null;

    const flush = () => {
      frame = 0;
      if (!pending) return;
      const { el, x, y } = pending;
      pending = null;
      const rect = el.getBoundingClientRect();
      el.style.setProperty('--sb-sx', `${Math.round(x - rect.left)}px`);
      el.style.setProperty('--sb-sy', `${Math.round(y - rect.top)}px`);
    };

    const onPointerMove = (event: PointerEvent) => {
      const target = event.target as Element | null;
      const el = target?.closest?.('[data-sb-sheen]') as HTMLElement | null;
      if (!el) return;
      pending = { el, x: event.clientX, y: event.clientY };
      if (!frame) frame = window.requestAnimationFrame(flush);
    };

    document.addEventListener('pointermove', onPointerMove, { passive: true });
    return () => {
      document.removeEventListener('pointermove', onPointerMove);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, []);
}

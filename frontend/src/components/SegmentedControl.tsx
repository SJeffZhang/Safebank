import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';
import styles from './SegmentedControl.module.css';

export interface SegmentOption {
  value: string;
  label: string;
}

export interface SegmentedControlProps {
  label: string;
  options: SegmentOption[];
  value: string;
  onChange: (value: string) => void;
  /** Optional id of the panel controlled by this tablist. */
  panelId?: string;
  className?: string;
}

interface IndicatorBox {
  left: number;
  width: number;
}

/**
 * iOS 风格分段控件：选中态是一个独立滑块，切换时滑到新位置。
 *
 * 滑块几何用「实测选中格」而不是「总宽 ÷ 格数」：中文标签宽度不等
 * （「持仓 5」比「发现」宽），等分会算错位置。用 ResizeObserver 跟随
 * 容器变化，避免字号或容器宽度改变后滑块跑位。
 */
export function SegmentedControl({
  label,
  options,
  value,
  onChange,
  panelId,
  className,
}: SegmentedControlProps) {
  const refs = useRef(new Map<string, HTMLButtonElement>());
  const rootRef = useRef<HTMLDivElement>(null);
  const [box, setBox] = useState<IndicatorBox>({ left: 0, width: 0 });

  const activate = (index: number) => {
    const next = options[(index + options.length) % options.length];
    if (!next) return;
    onChange(next.value);
    refs.current.get(next.value)?.focus();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const index = options.findIndex((option) => option.value === value);
    switch (event.key) {
      case 'ArrowRight':
        event.preventDefault();
        activate(index + 1);
        break;
      case 'ArrowLeft':
        event.preventDefault();
        activate(index - 1);
        break;
      case 'Home':
        event.preventDefault();
        activate(0);
        break;
      case 'End':
        event.preventDefault();
        activate(options.length - 1);
        break;
      default:
        break;
    }
  };

  // 位置/宽度依赖真实排版，绘制前量一次；容器尺寸变化时再量。
  useLayoutEffect(() => {
    const root = rootRef.current;
    const measure = () => {
      const node = refs.current.get(value);
      if (!node) return;
      const next = { left: node.offsetLeft, width: node.offsetWidth };
      setBox((current) =>
        current.left === next.left && current.width === next.width ? current : next,
      );
    };

    measure();
    if (typeof ResizeObserver === 'undefined' || !root) return undefined;
    const observer = new ResizeObserver(measure);
    observer.observe(root);
    return () => observer.disconnect();
  }, [value, options.length]);

  // 字体加载完成后字宽会变，补测一次，避免滑块停在旧位置。
  useEffect(() => {
    const fonts = (document as Document & { fonts?: { ready?: Promise<unknown> } }).fonts;
    if (!fonts?.ready) return undefined;
    let alive = true;
    void fonts.ready.then(() => {
      if (!alive) return;
      const node = refs.current.get(value);
      if (node) setBox({ left: node.offsetLeft, width: node.offsetWidth });
    });
    return () => {
      alive = false;
    };
  }, [value]);

  return (
    <div
      ref={rootRef}
      className={[styles.root, className ?? ''].filter(Boolean).join(' ')}
      role="tablist"
      aria-label={label}
      onKeyDown={onKeyDown}
    >
      {/* 选中滑块：left/width 由实测几何写入，切换时用弹簧缓动滑过去 */}
      <span
        className={styles.indicator}
        aria-hidden="true"
        style={{ transform: `translateX(${box.left}px)`, width: box.width }}
      />
      {options.map((option) => {
        const selected = option.value === value;
        return (
          <button
            key={option.value}
            ref={(node) => {
              if (node) refs.current.set(option.value, node);
              else refs.current.delete(option.value);
            }}
            type="button"
            role="tab"
            id={`${label}-${option.value}`}
            aria-selected={selected}
            aria-controls={panelId}
            tabIndex={selected ? 0 : -1}
            className={[styles.tab, selected ? styles.selected : ''].filter(Boolean).join(' ')}
            onClick={() => onChange(option.value)}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

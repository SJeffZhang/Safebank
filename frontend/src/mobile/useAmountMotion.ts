import { useLayoutEffect, useRef } from "react";
import { formatCents } from "../services/transfer/money";

/** Display-only interpolation. Never feeds balances, form values, or approval data. */
export function useAmountMotion(value: number, enabled: boolean, hidden: boolean) {
  const element = useRef<HTMLSpanElement>(null);
  const visible = useRef(0);
  useLayoutEffect(() => {
    const node = element.current;
    if (!node) return;
    if (hidden) { visible.current = 0; node.textContent = "••••••"; return; }
    const media = typeof matchMedia === "function" ? matchMedia("(prefers-reduced-motion: reduce)") : undefined;
    let frame = 0;
    let stopped = false;
    const write = (amount: number) => {visible.current = amount; node.textContent = formatCents(amount);};
    const finish = () => { stopped = true; cancelAnimationFrame(frame); write(value); };
    if (!enabled || !media || media.matches || typeof requestAnimationFrame !== "function" || !Number.isSafeInteger(value)) {
      write(value); return;
    }
    const from = visible.current;
    if (from === value) { write(value); return; }
    write(from);
    let start: number | undefined;
    const duration = from === 0 ? 1600 : 960;
    const tick = (time: number) => {
      if (stopped) return;
      start ??= time;
      const progress = Math.min(1, Math.max(0, (time - start) / duration));
      // Reach 90% at a steady pace, then reserve a longer ease-out for the final digits.
      const tail = Math.max(0, (progress - .65) / .35);
      const eased = progress < .65 ? .9 * progress / .65 : .9 + .1 * (1 - (1 - tail) ** 2);
      // Integer interpolation also preserves exact cents near Number.MAX_SAFE_INTEGER.
      const weight = BigInt(Math.round(eased * 10000));
      write(Number(BigInt(from) + (BigInt(value) - BigInt(from)) * weight / 10000n));
      if (progress < 1) frame = requestAnimationFrame(tick);
      else write(value);
    };
    const reduce = () => { if (media.matches) finish(); };
    media.addEventListener?.("change", reduce);
    frame = requestAnimationFrame(tick);
    return () => { stopped = true; cancelAnimationFrame(frame); media.removeEventListener?.("change", reduce); };
  }, [value, enabled, hidden]);
  return element;
}

import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Money } from "./ui";

let frames = new Map<number, FrameRequestCallback>();
let next = 0;
let reduced = false;
beforeEach(() => {
  frames = new Map(); next = 0; reduced = false;
  vi.stubGlobal("requestAnimationFrame", vi.fn((fn: FrameRequestCallback) => { frames.set(++next, fn); return next; }));
  vi.stubGlobal("cancelAnimationFrame", vi.fn((id: number) => frames.delete(id)));
  vi.stubGlobal("matchMedia", vi.fn(() => ({matches:reduced,addEventListener:vi.fn(),removeEventListener:vi.fn()})));
});
afterEach(() => vi.unstubAllGlobals());
function tick(time:number) { act(() => {const batch=[...frames.values()]; frames.clear(); batch.forEach(fn=>fn(time));}); }
function digits(container:HTMLElement) {return container.querySelector(".money-value")!.textContent;}

it("advances steadily first and gives the last ten percent a slower settling phase", () => {
  const {container}=render(<Money value={100000}/>);
  const amount=()=>Number(digits(container)!.replace(/,/g, ""));
  tick(0);tick(300);const early=amount();tick(600);const middle=amount();
  expect(Math.abs((middle-early)-early)).toBeLessThan(0.1);
  tick(1040);expect(amount()).toBe(900);
  tick(1200);const tail1=amount();tick(1360);const tail2=amount();tick(1520);const tail3=amount();
  expect(tail2-tail1).toBeGreaterThan(tail3-tail2);
  expect(tail3).toBeLessThan(1000);
  tick(1600);expect(digits(container)).toBe("1,000.00");expect(frames.size).toBe(0);
});

it("counts in on entry, retargets from the visible amount, and ends on exact cents", () => {
  const {container,rerender}=render(<Money value={100000}/>);
  expect(digits(container)).toBe("0.00");
  expect(screen.getByRole("img",{name:"金额 ¥ 1,000.00"})).toBeInTheDocument();
  tick(0);tick(600);
  const midway=digits(container);
  expect(midway).not.toBe("0.00");expect(midway).not.toBe("1,000.00");
  rerender(<Money value={12345}/>);
  expect(digits(container)).toBe(midway);
  tick(601);tick(1801);
  expect(digits(container)).toBe("123.45");
  expect(frames.size).toBe(0);
});

it("hides immediately mid-animation, cancels frames, and never exposes the hidden amount to assistive technology", () => {
  const {container,rerender}=render(<Money value={99999}/>);tick(0);tick(100);
  rerender(<Money value={99999} hide/>);
  expect(digits(container)).toBe("••••••");expect(frames.size).toBe(0);
  expect(screen.getByRole("img",{name:"金额已隐藏"})).toBeInTheDocument();
  expect(container.innerHTML).not.toContain("999.99");
});

it("keeps reduced-motion and confirmation amounts static, and cancels on unmount", () => {
  reduced=true;
  const first=render(<Money value={12345}/>);
  expect(digits(first.container)).toBe("123.45");expect(frames.size).toBe(0);first.unmount();
  reduced=false;
  const fixed=render(<Money value={99999} animate={false}/>);
  expect(digits(fixed.container)).toBe("999.99");expect(frames.size).toBe(0);fixed.unmount();
  const moving=render(<Money value={10000}/>);
  expect(frames.size).toBe(1);moving.unmount();expect(frames.size).toBe(0);
});

import { afterEach, expect, it, vi } from 'vitest';
import { revealTheme } from '../app/themeTransition';
const originalStart = document.startViewTransition;
const originalAnimate = document.documentElement.animate;
const originalMatch = window.matchMedia;
afterEach(()=>{
 Object.defineProperty(document,'startViewTransition',{configurable:true,value:originalStart});
 document.documentElement.animate=originalAnimate;
 window.matchMedia=originalMatch;
 document.querySelector('[data-theme-toggle]')?.remove();
 vi.restoreAllMocks();
});
function motion(reduced=false){window.matchMedia=vi.fn().mockReturnValue({matches:reduced});}
it('switches immediately when View Transitions are unsupported',async()=>{
 motion();Object.defineProperty(document,'startViewTransition',{configurable:true,value:undefined});
 const commit=vi.fn();await revealTheme(commit);expect(commit).toHaveBeenCalledTimes(1);
 expect(document.documentElement).not.toHaveClass('theme-reveal-active');
});
it('respects reduced motion without starting any screenshot transition',async()=>{
 motion(true);const start=vi.fn();Object.defineProperty(document,'startViewTransition',{configurable:true,value:start});
 const commit=vi.fn();await revealTheme(commit);expect(commit).toHaveBeenCalledTimes(1);expect(start).not.toHaveBeenCalled();
});
it('uses the switch centre and a radius that reaches all viewport corners, then removes temporary styles',async()=>{
 motion();const button=document.createElement('button');button.dataset.themeToggle='';document.body.append(button);
 vi.spyOn(button,'getBoundingClientRect').mockReturnValue({left:300,top:20,width:40,height:40} as DOMRect);
 let origin='';const commit=vi.fn(()=>{origin=document.documentElement.style.getPropertyValue('--sb-theme-origin-x');});
 const start=vi.fn((update:()=>void)=>{update();return {ready:Promise.resolve(),finished:Promise.resolve(),skipTransition:vi.fn()};});
 Object.defineProperty(document,'startViewTransition',{configurable:true,value:start});
 const animate=vi.fn().mockReturnValue({finished:Promise.resolve()});document.documentElement.animate=animate;
 await revealTheme(commit);
 expect(commit).toHaveBeenCalledTimes(1);expect(origin).toBe('320px');
 const radius=Math.hypot(Math.max(320,window.innerWidth-320),Math.max(40,window.innerHeight-40))+24;
 expect(animate).toHaveBeenCalledWith({'--sb-theme-radius':['0px',`${radius}px`]},expect.objectContaining({duration:1400,pseudoElement:'::view-transition-new(root)'}));
 expect(document.documentElement).not.toHaveClass('theme-reveal-active');
 expect(document.documentElement.style.getPropertyValue('--sb-theme-origin-x')).toBe('');
});
it('a failed snapshot still commits once and clears the transition instead of trapping the theme',async()=>{
 motion();const skip=vi.fn();const commit=vi.fn();
 Object.defineProperty(document,'startViewTransition',{configurable:true,value:(update:()=>void)=>{update();return {ready:Promise.reject(new Error('capture failed')),finished:Promise.resolve(),skipTransition:skip};}});
 await revealTheme(commit);expect(commit).toHaveBeenCalledTimes(1);expect(skip).toHaveBeenCalledTimes(1);
 expect(document.documentElement).not.toHaveClass('theme-reveal-active');
});

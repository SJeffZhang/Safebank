import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';

// jsdom 不实现 scrollTo / scrollY。用 no-op 让路由壳挂载时不产生 not-implemented 噪音；
// 需要断言滚动行为的用例会在各自测试里用 vi.fn 覆盖 scrollTo 并控制 scrollY。
if (typeof window !== 'undefined') {
  Object.defineProperty(window, 'scrollTo', {
    value: () => {},
    writable: true,
    configurable: true,
  });
  Object.defineProperty(window, 'scrollY', { value: 0, writable: true, configurable: true });
}

afterEach(() => {
  cleanup();
});
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useScrollRestore } from '../app/hooks';
import { clearScrollMemory } from '../app/scrollMemory';

const hoisted = vi.hoisted(() => ({
  top: 0,
  calls: [] as number[],
}));

vi.mock('../app/scroll', () => ({
  applyScroll: (top: number) => {
    hoisted.calls.push(top);
  },
  readScrollTop: () => hoisted.top,
}));

function Harness() {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  useScrollRestore(pathname);
  return (
    <div>
      <span data-testid="path">{pathname}</span>
      <button type="button" onClick={() => navigate('/')}>
        去首页
      </button>
      <button type="button" onClick={() => navigate('/assistant')}>
        去小安
      </button>
      <button type="button" onClick={() => navigate('/wealth')}>
        去理财
      </button>
      <button type="button" onClick={() => navigate('/wealth/holdings')}>
        去全部持仓
      </button>
    </div>
  );
}

function renderHarness(initial = '/') {
  return render(
    <MemoryRouter initialEntries={[initial]}>
      <Routes>
        <Route path="*" element={<Harness />} />
      </Routes>
    </MemoryRouter>,
  );
}

function lastCall(): number {
  return hoisted.calls[hoisted.calls.length - 1];
}

/** 模拟用户滚动：更新当前 scrollTop 并派发 scroll 事件，让 Hook 的监听器同步记录。 */
function setScroll(top: number) {
  hoisted.top = top;
  window.dispatchEvent(new Event('scroll'));
}

describe('useScrollRestore navigation behavior', () => {
  beforeEach(() => {
    clearScrollMemory();
    hoisted.top = 0;
    hoisted.calls.length = 0;
  });

  it('restores home position after home -> assistant -> home', async () => {
    const user = userEvent.setup();
    renderHarness('/');
    expect(lastCall()).toBe(0);

    setScroll(500); // 首页滚到 500
    await user.click(screen.getByRole('button', { name: '去小安' }));
    expect(lastCall()).toBe(0); // 小安首次进入 → 顶部

    setScroll(0); // 小安当前在顶部
    await user.click(screen.getByRole('button', { name: '去首页' }));
    expect(lastCall()).toBe(500); // 首页恢复 500
  });

  it('keeps first visit to assistant at top and does not reuse home position', async () => {
    const user = userEvent.setup();
    renderHarness('/');
    setScroll(320);
    await user.click(screen.getByRole('button', { name: '去小安' }));
    expect(lastCall()).toBe(0);

    await user.click(screen.getByRole('button', { name: '去理财' }));
    expect(lastCall()).toBe(0);
  });

  it('restores wealth position after wealth -> holdings -> wealth', async () => {
    const user = userEvent.setup();
    renderHarness('/');
    hoisted.calls.length = 0;

    await user.click(screen.getByRole('button', { name: '去理财' }));
    expect(lastCall()).toBe(0);

    setScroll(300);
    await user.click(screen.getByRole('button', { name: '去全部持仓' }));
    expect(lastCall()).toBe(0);

    await user.click(screen.getByRole('button', { name: '去理财' }));
    expect(lastCall()).toBe(300);
  });

  it('does not mix positions between paths', async () => {
    const user = userEvent.setup();
    renderHarness('/');
    hoisted.calls.length = 0;

    setScroll(600); // 首页滚到 600
    await user.click(screen.getByRole('button', { name: '去理财' }));
    expect(lastCall()).toBe(0); // 理财首次进入 0，不继承 600

    setScroll(90); // 理财滚到 90
    await user.click(screen.getByRole('button', { name: '去首页' }));
    expect(lastCall()).toBe(600); // 首页恢复 600，而非 90
  });
});
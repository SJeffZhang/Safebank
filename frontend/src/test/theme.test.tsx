import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it } from 'vitest';
import { ThemeProvider } from '../app/ThemeProvider';
import { ToastProvider } from '../components/Toast';
import ComponentsPage from '../pages/dev/ComponentsPage';

function renderApp() {
  return render(
    <ThemeProvider>
      <ToastProvider>
        <MemoryRouter initialEntries={['/dev/components']}>
          <Routes>
            <Route path="/dev/components" element={<ComponentsPage />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </ThemeProvider>,
  );
}

describe('Theme and navigation', () => {
  beforeEach(() => {
    window.localStorage.clear();
    document.documentElement.removeAttribute('data-theme');
  });

  it('toggles between light and dark and persists the choice', async () => {
    const user = userEvent.setup();
    renderApp();

    expect(document.documentElement.dataset.theme).toBe('light');

    await user.click(screen.getByRole('button', { name: '切换为深色主题' }));
    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(window.localStorage.getItem('sb-theme')).toBe('dark');
    expect(screen.getByRole('button', { name: '切换为浅色主题' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );

    await user.click(screen.getByRole('button', { name: '切换为浅色主题' }));
    expect(document.documentElement.dataset.theme).toBe('light');
    expect(window.localStorage.getItem('sb-theme')).toBe('light');
  });

  it('keeps the primary navigation to exactly three tabs', () => {
    renderApp();
    const nav = screen.getByRole('navigation', { name: '主导航' });
    const items = within(nav).getAllByRole('button');
    expect(items).toHaveLength(3);
    expect(items.map((item) => item.textContent)).toEqual(['首页', '小安', '个人']);
    expect(within(nav).queryByText('理财')).toBeNull();
    expect(within(nav).queryByText('卡片')).toBeNull();
  });

  it('exposes the current tab with aria-current', async () => {
    const user = userEvent.setup();
    renderApp();
    const nav = screen.getByRole('navigation', { name: '主导航' });
    const home = within(nav).getByRole('button', { name: '首页' });
    expect(home).toHaveAttribute('aria-current', 'page');

    await user.click(within(nav).getByRole('button', { name: '小安' }));
    expect(within(nav).getByRole('button', { name: '小安' })).toHaveAttribute(
      'aria-current',
      'page',
    );
    expect(home).not.toHaveAttribute('aria-current');
  });
});
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
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

function resultButtons() {
  const list = screen.getByRole('list', { name: '产品列表' });
  return within(list).getAllByRole('button');
}

describe('product search', () => {
  it('starts by showing all four products', () => {
    renderApp();
    expect(resultButtons()).toHaveLength(4);
  });

  it('shows an empty state for a keyword with no matches', async () => {
    const user = userEvent.setup();
    renderApp();

    await user.type(screen.getByLabelText('搜索产品'), '不存在的产品');
    expect(screen.getByText(/没有找到/)).toBeInTheDocument();
    expect(screen.queryByRole('list', { name: '产品列表' })).not.toBeInTheDocument();
  });

  it('restores all four products from the empty-state action', async () => {
    const user = userEvent.setup();
    renderApp();

    await user.type(screen.getByLabelText('搜索产品'), '不存在的产品');
    await user.click(screen.getByRole('button', { name: '清除搜索' }));

    expect(screen.queryByText(/没有找到/)).not.toBeInTheDocument();
    expect(screen.getByLabelText('搜索产品')).toHaveValue('');
    expect(resultButtons()).toHaveLength(4);
  });

  it('restores all four products from the input clear button', async () => {
    const user = userEvent.setup();
    renderApp();

    await user.type(screen.getByLabelText('搜索产品'), '不存在的产品');
    await user.click(screen.getByRole('button', { name: '清空搜索' }));

    expect(screen.queryByText(/没有找到/)).not.toBeInTheDocument();
    expect(screen.getByLabelText('搜索产品')).toHaveValue('');
    expect(resultButtons()).toHaveLength(4);
  });

  it('filters by risk level (R3)', async () => {
    const user = userEvent.setup();
    renderApp();

    await user.type(screen.getByLabelText('搜索产品'), 'R3');
    const buttons = resultButtons();
    expect(buttons).toHaveLength(2);
    expect(buttons[0]).toHaveTextContent('稳健增利');
    expect(buttons[1]).toHaveTextContent('中证红利');
  });

  it('filters by product name', async () => {
    const user = userEvent.setup();
    renderApp();

    await user.type(screen.getByLabelText('搜索产品'), '现金');
    const buttons = resultButtons();
    expect(buttons).toHaveLength(1);
    expect(buttons[0]).toHaveTextContent('现金管理');
  });
});
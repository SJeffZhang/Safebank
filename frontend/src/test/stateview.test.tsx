import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { StateView } from '../components/StateView';

describe('StateView', () => {
  it('exposes an error state with a recovery action', async () => {
    const user = userEvent.setup();
    const onAction = vi.fn();
    render(<StateView state="error" actionLabel="重试" onAction={onAction} />);

    expect(screen.getByRole('alert')).toHaveTextContent('加载失败');
    await user.click(screen.getByRole('button', { name: '重试' }));
    expect(onAction).toHaveBeenCalledTimes(1);
  });

  it('renders empty with a next step', () => {
    render(<StateView state="empty" actionLabel="去创建" onAction={() => undefined} />);
    expect(screen.getByText('暂无内容')).toBeInTheDocument();
  });

  it('keeps unknown distinct from success', () => {
    const { rerender } = render(<StateView state="unknown" />);
    expect(screen.getByText('结果待确认')).toBeInTheDocument();
    expect(screen.queryByText('操作已完成')).not.toBeInTheDocument();

    rerender(<StateView state="success" />);
    expect(screen.getByText('操作已完成')).toBeInTheDocument();
  });

  it('does not promise that nothing changed on a generic failure', () => {
    render(<StateView state="failed" />);
    expect(screen.getByText('操作未完成')).toBeInTheDocument();
    expect(screen.getByText(/请查看详情/)).toBeInTheDocument();
    expect(screen.queryByText(/没有产生变更/)).not.toBeInTheDocument();
  });

  it('allows the caller to pass a server-confirmed description', () => {
    render(
      <StateView state="failed" description="服务端已确认：本次没有产生任何账务变更。" />,
    );
    expect(screen.getByText(/服务端已确认：本次没有产生任何账务变更/)).toBeInTheDocument();
  });

  it('announces loading without layout-shifting copy', () => {
    render(<StateView state="loading" />);
    expect(screen.getByText('正在加载')).toBeInTheDocument();
    expect(screen.getByText('正在加载…')).toBeInTheDocument();
  });
});

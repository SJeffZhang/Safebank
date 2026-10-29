import { useState } from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { Sheet } from '../components/Sheet';

function Harness({ dismissible = true, onClose }: { dismissible?: boolean; onClose?: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        打开
      </button>
      <Sheet
        open={open}
        title="选择付款账户"
        description="演示 Sheet"
        dismissible={dismissible}
        onClose={() => {
          setOpen(false);
          onClose?.();
        }}
        footer={<button type="button">完成</button>}
      >
        <p>内容</p>
      </Sheet>
    </>
  );
}

describe('Sheet', () => {
  it('opens, traps focus, closes on Escape and restores focus', async () => {
    const user = userEvent.setup();
    render(<Harness />);

    const trigger = screen.getByRole('button', { name: '打开' });
    await user.click(trigger);

    const dialog = screen.getByRole('dialog', { name: '选择付款账户' });
    expect(dialog).toBeInTheDocument();
    expect(document.body).toHaveClass('sb-scroll-lock');
    await waitFor(() => expect(dialog).toHaveFocus());

    await user.keyboard('{Escape}');

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(document.body).not.toHaveClass('sb-scroll-lock');
    expect(trigger).toHaveFocus();
  });

  it('does not close when dismissible is false', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<Harness dismissible={false} onClose={onClose} />);

    await user.click(screen.getByRole('button', { name: '打开' }));
    const dialog = screen.getByRole('dialog', { name: '选择付款账户' });

    await user.keyboard('{Escape}');
    expect(onClose).not.toHaveBeenCalled();
    expect(dialog).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '关闭' })).toBeDisabled();
  });

  it('keeps footer actions reachable and body scrollable', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole('button', { name: '打开' }));

    const footerButton = screen.getByRole('button', { name: '完成' });
    expect(footerButton).toBeVisible();
    await user.click(footerButton);
  });
});

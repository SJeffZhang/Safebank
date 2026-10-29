import { useState } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { Button, IconButton } from '../components/Button';
import { Icon } from '../components/Icon';

describe('Button', () => {
  it('does not fire again while loading', async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    const { rerender } = render(<Button onClick={onClick}>确认</Button>);

    await user.click(screen.getByRole('button', { name: '确认' }));
    expect(onClick).toHaveBeenCalledTimes(1);

    rerender(
      <Button onClick={onClick} loading>
        确认
      </Button>,
    );
    const button = screen.getByRole('button', { name: '确认' });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute('aria-busy', 'true');

    await user.click(button);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it('keeps icon-only buttons named for assistive tech', () => {
    render(
      <IconButton label="新增收款人">
        <Icon name="plus" />
      </IconButton>,
    );
    expect(screen.getByRole('button', { name: '新增收款人' })).toBeInTheDocument();
  });

  it('supports disabled state', async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(
      <Button disabled onClick={onClick}>
        禁用
      </Button>,
    );
    const button = screen.getByRole('button', { name: '禁用' });
    expect(button).toBeDisabled();
    await user.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });
});

describe('Button variants', () => {
  it('renders all variants without losing the label', () => {
    const { rerender } = render(<Button variant="primary">主操作</Button>);
    expect(screen.getByRole('button', { name: '主操作' })).toBeInTheDocument();
    rerender(<Button variant="danger">危险</Button>);
    expect(screen.getByRole('button', { name: '危险' })).toBeInTheDocument();
  });
});

function FormHarness() {
  const [value, setValue] = useState('');
  return (
    <input
      aria-label="受控输入"
      value={value}
      onChange={(event) => setValue(event.target.value)}
    />
  );
}

describe('Harness sanity', () => {
  it('supports controlled input', async () => {
    const user = userEvent.setup();
    render(<FormHarness />);
    const input = screen.getByRole('textbox', { name: '受控输入' });
    await user.type(input, '12');
    expect(input).toHaveValue('12');
  });
});

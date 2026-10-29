import { useState } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { AmountText } from '../components/AmountText';

function Harness() {
  const [hidden, setHidden] = useState(false);
  return (
    <AmountText
      amountCents={123456}
      hidden={hidden}
      onToggleHidden={() => setHidden((value) => !value)}
      label="总资产"
    />
  );
}

describe('AmountText', () => {
  it('formats cents with tabular currency', () => {
    render(<AmountText amountCents={123456} />);
    expect(screen.getByText('¥1,234.56')).toBeInTheDocument();
  });

  it('hides and restores the amount without exposing the value', async () => {
    const user = userEvent.setup();
    render(<Harness />);

    expect(screen.getByText('¥1,234.56')).toBeInTheDocument();
    const toggle = screen.getByRole('button', { name: '隐藏总资产' });
    expect(toggle).toHaveAttribute('aria-pressed', 'false');

    await user.click(toggle);

    expect(screen.queryByText('¥1,234.56')).not.toBeInTheDocument();
    expect(screen.getByText('总资产已隐藏')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '显示总资产' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );

    await user.click(screen.getByRole('button', { name: '显示总资产' }));
    expect(screen.getByText('¥1,234.56')).toBeInTheDocument();
  });

  it('marks gains and losses with data-trend, not colour alone', () => {
    const { container } = render(
      <>
        <AmountText amountCents={100} signed />
        <AmountText amountCents={-100} signed />
      </>,
    );
    const values = container.querySelectorAll('[data-trend]');
    expect(values[0]).toHaveAttribute('data-trend', 'up');
    expect(values[1]).toHaveAttribute('data-trend', 'down');
    expect(values[0]).toHaveTextContent('+¥1.00');
    expect(values[1]).toHaveTextContent('-¥1.00');
  });
});

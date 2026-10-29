import { useState } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { SegmentedControl } from '../components/SegmentedControl';

function Harness() {
  const [value, setValue] = useState('holdings');
  return (
    <SegmentedControl
      label="理财视图"
      options={[
        { value: 'holdings', label: '持仓' },
        { value: 'discover', label: '发现' },
      ]}
      value={value}
      onChange={setValue}
    />
  );
}

describe('SegmentedControl', () => {
  it('moves selection with arrow keys and keeps ARIA in sync', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const holdings = screen.getByRole('tab', { name: '持仓' });
    const discover = screen.getByRole('tab', { name: '发现' });

    expect(holdings).toHaveAttribute('aria-selected', 'true');
    expect(holdings).toHaveAttribute('tabindex', '0');
    expect(discover).toHaveAttribute('tabindex', '-1');

    holdings.focus();
    await user.keyboard('{ArrowRight}');

    expect(discover).toHaveAttribute('aria-selected', 'true');
    expect(discover).toHaveAttribute('tabindex', '0');
    expect(discover).toHaveFocus();
    expect(holdings).toHaveAttribute('tabindex', '-1');

    await user.keyboard('{ArrowLeft}');
    expect(holdings).toHaveAttribute('aria-selected', 'true');
    expect(holdings).toHaveFocus();
  });

  it('supports Home and End', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const holdings = screen.getByRole('tab', { name: '持仓' });
    const discover = screen.getByRole('tab', { name: '发现' });

    holdings.focus();
    await user.keyboard('{End}');
    expect(discover).toHaveAttribute('aria-selected', 'true');
    expect(discover).toHaveFocus();

    await user.keyboard('{Home}');
    expect(holdings).toHaveAttribute('aria-selected', 'true');
    expect(holdings).toHaveFocus();
  });
});

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { TextField } from '../components/TextField';

describe('TextField', () => {
  it('associates label, hint and error', () => {
    render(<TextField label="金额" hint="最多两位小数" error="金额超过单笔限额" />);
    const input = screen.getByLabelText('金额');
    const error = screen.getByRole('alert');

    expect(input).toHaveAttribute('aria-invalid', 'true');
    expect(input).toHaveAccessibleName('金额');
    expect(input).toHaveAccessibleDescription(/最多两位小数/);
    expect(input.getAttribute('aria-describedby')).toContain(error.id);
    expect(error).toHaveTextContent('金额超过单笔限额');
  });

  it('supports disabled state', () => {
    render(<TextField label="备注" defaultValue="不可编辑" disabled />);
    expect(screen.getByLabelText('备注')).toBeDisabled();
  });

  it('does not rely on placeholder for the accessible name', () => {
    render(<TextField label="收款人" placeholder="姓名或手机号" />);
    expect(screen.getByRole('textbox', { name: '收款人' })).toBeInTheDocument();
  });
});

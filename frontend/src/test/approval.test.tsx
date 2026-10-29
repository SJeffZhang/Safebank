import { useState } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ApprovalSheet } from '../components/ApprovalSheet';
import type { ApprovalState, ApprovalSummary } from '../components/ApprovalSheet';

const summary: ApprovalSummary = {
  title: '向王五转账',
  amountText: '¥3,000.00',
  rows: [{ label: '收款人', value: '王五' }],
};

function renderSheet(overrides: {
  state?: ApprovalState;
  mode?: 'yellow' | 'red';
  onConfirm?: () => void;
  onClose?: () => void;
  onViewStatus?: () => void;
}) {
  return render(
    <ApprovalSheet
      open
      approvalId="apv_test"
      mode={overrides.mode ?? 'yellow'}
      state={overrides.state ?? 'ready'}
      summary={summary}
      riskAckRequired={overrides.mode === 'red'}
      onConfirm={overrides.onConfirm ?? (() => undefined)}
      onClose={overrides.onClose ?? (() => undefined)}
      onViewStatus={overrides.onViewStatus}
    />,
  );
}

describe('ApprovalSheet', () => {
  it('does not allow a second confirm while confirming', async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    renderSheet({ state: 'confirming', onConfirm });

    const confirm = screen.getByRole('button', { name: '确认中…' });
    expect(confirm).toBeDisabled();
    expect(confirm).toHaveAttribute('aria-busy', 'true');
    await user.click(confirm);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('renders UNKNOWN as neutral, never as success', () => {
    renderSheet({ state: 'unknown' });
    expect(screen.getByText('结果待确认')).toBeInTheDocument();
    expect(screen.queryByText('操作已完成')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '查看状态' })).toBeInTheDocument();
  });

  it('never offers a resend entry for red-level OTP', () => {
    renderSheet({ mode: 'red' });
    expect(screen.queryByText(/重新发送/)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /重新发送/ })).not.toBeInTheDocument();
  });

  it('requires risk acknowledgement and a six-digit OTP before confirming', async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();

    function RedHarness() {
      const [otp, setOtp] = useState('');
      return (
        <ApprovalSheet
          open
          approvalId="apv_test_red"
          mode="red"
          state="ready"
          summary={summary}
          riskAckRequired
          otpValue={otp}
          onOtpChange={setOtp}
          onConfirm={onConfirm}
          onClose={() => undefined}
        />
      );
    }

    render(<RedHarness />);

    const confirm = screen.getByRole('button', { name: '验证并确认' });
    expect(confirm).toBeDisabled();

    await user.click(screen.getByLabelText(/我已阅读并理解/));
    expect(confirm).toBeDisabled();

    await user.type(screen.getByLabelText(/短信验证码/), '123456');
    expect(confirm).toBeEnabled();

    await user.click(confirm);
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it('keeps yellow-level confirmation behind the explicit button', async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    renderSheet({ onConfirm });

    await user.click(screen.getByRole('button', { name: '确认操作' }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });
  it('does not claim that no funds moved on a generic failure', () => {
    renderSheet({ state: 'failed' });
    expect(screen.getByText('操作未完成')).toBeInTheDocument();
    expect(screen.getByText(/请查看详情/)).toBeInTheDocument();
    expect(screen.queryByText(/未产生任何资金变动/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重新尝试' })).toBeInTheDocument();
  });
});
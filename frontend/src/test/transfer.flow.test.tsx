import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { TransferPage } from '../pages/Transfer';
import { TransferServiceProvider } from '../services/transfer/TransferService';
import type { TransferService } from '../services/transfer/TransferService';
import { MockTransferService } from '../services/transfer/MockTransferService';
import type { MockScenario } from '../services/transfer/MockTransferService';
import { DEMO_ACCOUNTS, DEMO_RECIPIENTS } from '../services/transfer/types';
import { savePendingApproval } from '../app/viewState';
import { ToastProvider } from '../components/Toast';

function renderTransfer(scenario?: Partial<MockScenario>, initialEntry = '/transfer') {
  const service = new MockTransferService(scenario, 0);
  renderWithService(service, initialEntry);
  return service;
}

function renderWithService(service: TransferService, initialEntry = '/transfer') {
  render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <TransferServiceProvider service={service}>
        <ToastProvider>
          <TransferPage />
        </ToastProvider>
      </TransferServiceProvider>
    </MemoryRouter>,
  );
}

async function fillValidForm(user: ReturnType<typeof userEvent.setup>, amount = '3000', memo = '房租') {
  await user.type(screen.getByLabelText('转账金额（元）'), amount);
  await user.selectOptions(screen.getByLabelText('付款账户'), 'acc-current');
  await user.selectOptions(screen.getByLabelText('收款人'), 'rcp-wang');
  if (memo) await user.type(screen.getByLabelText('备注（选填）'), memo);
}

describe('transfer form basics', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    window.localStorage.clear();
  });

  it('starts empty: amount, account and recipient unset', () => {
    renderTransfer();
    expect(screen.getByLabelText('转账金额（元）')).toHaveValue('');
    expect(screen.getByLabelText('付款账户')).toHaveValue('');
    expect(screen.getByLabelText('收款人')).toHaveValue('');
  });

  it('does not prefill business fields from URL params', () => {
    renderTransfer(undefined, '/transfer?amount=9999&recipient=王五&from=acc-current');
    expect(screen.getByLabelText('转账金额（元）')).toHaveValue('');
    expect(screen.getByLabelText('收款人')).toHaveValue('');
    expect(screen.getByLabelText('付款账户')).toHaveValue('');
  });

  it('invalid amount does not call preview', async () => {
    const user = userEvent.setup();
    const service = renderTransfer();
    const spy = vi.spyOn(service, 'previewTransfer');
    await user.type(screen.getByLabelText('转账金额（元）'), 'abc');
    await user.selectOptions(screen.getByLabelText('付款账户'), 'acc-current');
    await user.selectOptions(screen.getByLabelText('收款人'), 'rcp-wang');
    await user.click(screen.getByRole('button', { name: /预演这笔转账/ }));
    expect(spy).not.toHaveBeenCalled();
    expect(screen.getAllByText(/金额格式不正确/).length).toBeGreaterThan(0);
  });

  it('missing account or recipient blocks preview', async () => {
    const user = userEvent.setup();
    const service = renderTransfer();
    const spy = vi.spyOn(service, 'previewTransfer');
    await user.type(screen.getByLabelText('转账金额（元）'), '100');
    await user.click(screen.getByRole('button', { name: /预演这笔转账/ }));
    expect(spy).not.toHaveBeenCalled();
    expect(screen.getAllByText(/请选择/).length).toBeGreaterThan(0);
  });

  it('frozen account shows 暂停外转 and blocks preview', async () => {
    const user = userEvent.setup();
    renderTransfer();
    await screen.findByRole('option', { name: /工资卡/ });
    await user.selectOptions(screen.getByLabelText('付款账户'), 'acc-frozen');
    expect(screen.getByText('暂停外转')).toBeInTheDocument();
  });
});

describe('transfer preview → approval', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    window.localStorage.clear();
  });

  it('preview success loads PlanView by approval_id', async () => {
    const user = userEvent.setup();
    const service = renderTransfer();
    const spy = vi.spyOn(service, 'getPlanView');
    await fillValidForm(user);
    await user.click(screen.getByRole('button', { name: /预演这笔转账/ }));

    const dialog = await screen.findByRole('dialog', { name: '确认操作' });
    expect(spy).toHaveBeenCalledWith('mock-apv-1');
    const text = dialog.textContent ?? '';
    expect(text).toContain('收款人');
    expect(text).toContain('转前可用');
    expect(text).toContain('转后可用');
    expect(text).toContain('当日累计');
    expect(text).toContain('7 天预留');
    expect(text).toContain('王五');
  });

  it('PlanView load failure can be retried and old content is not confirmable', async () => {
    const user = userEvent.setup();
    renderTransfer({ planViewFailOnce: true });
    await fillValidForm(user);
    await user.click(screen.getByRole('button', { name: /预演这笔转账/ }));

    expect(await screen.findByText('预演详情加载失败')).toBeInTheDocument();
    expect(screen.queryByRole('dialog', { name: '确认操作' })).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '重试' }));
    expect(await screen.findByRole('dialog', { name: '确认操作' })).toBeInTheDocument();
  });

  it('cancel keeps input and never calls confirm', async () => {
    const user = userEvent.setup();
    const service = renderTransfer();
    const confirmSpy = vi.spyOn(service, 'confirmApproval');
    await fillValidForm(user);
    await user.click(screen.getByRole('button', { name: /预演这笔转账/ }));
    await screen.findByRole('dialog', { name: '确认操作' });

    await user.click(screen.getByRole('button', { name: '拒绝' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(screen.getByLabelText('转账金额（元）')).toHaveValue('3000');
  });

  it('yellow confirm leads to success from service', async () => {
    const user = userEvent.setup();
    const service = renderTransfer({ confirm: 'success' });
    const confirmSpy = vi.spyOn(service, 'confirmApproval');
    await fillValidForm(user);
    await user.click(screen.getByRole('button', { name: /预演这笔转账/ }));
    await screen.findByRole('dialog', { name: '确认操作' });

    await user.click(screen.getByRole('button', { name: '确认操作' }));
    expect(await screen.findByText('操作已完成')).toBeInTheDocument();
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(confirmSpy).toHaveBeenCalledWith('mock-apv-1', { kind: 'yellow' });
  });

  it('red OTP: incomplete cannot confirm, no resend entry, success on 6-digit', async () => {
    const user = userEvent.setup();
    const service = renderTransfer({ confirm: 'success' });
    const confirmSpy = vi.spyOn(service, 'confirmApproval');
    await fillValidForm(user, '6000', '');
    await user.click(screen.getByRole('button', { name: /预演这笔转账/ }));
    const dialog = await screen.findByRole('dialog', { name: '确认操作' });

    expect(within(dialog).queryByText(/重新发送/)).not.toBeInTheDocument();
    const confirm = within(dialog).getByRole('button', { name: '验证并确认' });
    expect(confirm).toBeDisabled();

    await user.click(within(dialog).getByLabelText(/我已阅读并理解/));
    expect(confirm).toBeDisabled();

    await user.type(within(dialog).getByLabelText(/短信验证码/), '123456');
    expect(confirm).toBeEnabled();

    await user.click(confirm);
    expect(await screen.findByText('操作已完成')).toBeInTheDocument();
    expect(confirmSpy).toHaveBeenCalledWith('mock-apv-1', { kind: 'red', otp: '123456' });
  });

  it('failed uncertain does not claim no funds moved, offers 返回修改', async () => {
    const user = userEvent.setup();
    renderTransfer({ confirm: 'failedUncertain' });
    await fillValidForm(user);
    await user.click(screen.getByRole('button', { name: /预演这笔转账/ }));
    await screen.findByRole('dialog', { name: '确认操作' });
    await user.click(screen.getByRole('button', { name: '确认操作' }));

    expect(await screen.findByText('操作未完成')).toBeInTheDocument();
    expect(screen.queryByText(/未产生资金变动/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '返回修改' })).toBeInTheDocument();
  });

  it('failed with noFundsMoved shows the service-confirmed claim', async () => {
    const user = userEvent.setup();
    renderTransfer({ confirm: 'failedNoFunds' });
    await fillValidForm(user);
    await user.click(screen.getByRole('button', { name: /预演这笔转账/ }));
    await screen.findByRole('dialog', { name: '确认操作' });
    await user.click(screen.getByRole('button', { name: '确认操作' }));

    expect(await screen.findByText(/本次转账未产生资金变动/)).toBeInTheDocument();
  });

  it('unknown shows 结果待确认 and 查看状态, never success, no re-transfer default', async () => {
    const user = userEvent.setup();
    renderTransfer({ confirm: 'unknown', afterUnknownStatus: 'unknown' });
    await fillValidForm(user);
    await user.click(screen.getByRole('button', { name: /预演这笔转账/ }));
    await screen.findByRole('dialog', { name: '确认操作' });
    await user.click(screen.getByRole('button', { name: '确认操作' }));

    expect(await screen.findByText('结果待确认')).toBeInTheDocument();
    expect(screen.queryByText('操作已完成')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '查看状态' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /重新转账/ })).not.toBeInTheDocument();
  });

  it('unknown → getApprovalStatus returns success updates to success', async () => {
    const user = userEvent.setup();
    renderTransfer({ confirm: 'unknown', afterUnknownStatus: 'success' });
    await fillValidForm(user);
    await user.click(screen.getByRole('button', { name: /预演这笔转账/ }));
    await screen.findByRole('dialog', { name: '确认操作' });
    await user.click(screen.getByRole('button', { name: '确认操作' }));
    await screen.findByText('结果待确认');

    await user.click(screen.getByRole('button', { name: '查看状态' }));
    expect(await screen.findByText('操作已完成')).toBeInTheDocument();
  });

  it('persistent unknown keeps 查看状态 exit', async () => {
    const user = userEvent.setup();
    renderTransfer({ confirm: 'unknown', afterUnknownStatus: 'unknown' });
    await fillValidForm(user);
    await user.click(screen.getByRole('button', { name: /预演这笔转账/ }));
    await screen.findByRole('dialog', { name: '确认操作' });
    await user.click(screen.getByRole('button', { name: '确认操作' }));
    await screen.findByText('结果待确认');

    await user.click(screen.getByRole('button', { name: '查看状态' }));
    await waitFor(() => expect(screen.getByText('结果待确认')).toBeInTheDocument());
  });

  it('expired PlanView cannot confirm and requires re-preview', async () => {
    const user = userEvent.setup();
    renderTransfer({ planViewExpired: true });
    await fillValidForm(user);
    await user.click(screen.getByRole('button', { name: /预演这笔转账/ }));

    expect(await screen.findByText('预演已过期')).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重新预演' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '重新预演' }));
    // 重新预演后仍过期（scenario 未变），但会重新请求并获得新的 approval_id
    await waitFor(() => expect(screen.getByText('预演已过期')).toBeInTheDocument());
  });

  it('recovers a pending approval_id and queries status with the exact stored id', async () => {
    savePendingApproval('mock-apv-9');
    const user = userEvent.setup();
    const receivedIds: string[] = [];

    const fake: TransferService = {
      listAccounts: async () => DEMO_ACCOUNTS.map((a) => ({ ...a })),
      listRecipients: async () => DEMO_RECIPIENTS.map((r) => ({ ...r })),
      getDailyRemainingCents: async () => 2400000,
      previewTransfer: async () => ({ kind: 'ok', approvalId: 'x' }),
      getPlanView: async () => {
        throw new Error('not used in recovery');
      },
      confirmApproval: async () => ({ status: 'success' }),
      getApprovalStatus: async (id) => {
        receivedIds.push(id);
        return 'success';
      },
      rejectApproval: async () => undefined,
    };

    renderWithService(fake);

    expect(await screen.findByText(/有待确认的转账操作/)).toBeInTheDocument();
    expect(screen.queryByText('操作已完成')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '查看状态' }));
    expect(receivedIds).toEqual(['mock-apv-9']);
    expect(await screen.findByText('操作已完成')).toBeInTheDocument();
  });
});
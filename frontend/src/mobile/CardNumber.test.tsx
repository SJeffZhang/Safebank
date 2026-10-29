import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it, vi } from 'vitest';
import { Cards } from './Services';
import { read, getCardNumber } from './api';
import type { PageProps } from './Banking';
vi.mock('./api', () => ({ read: vi.fn(), getCardNumber: vi.fn(), isPreview: true }));
const pan = '6222000000000000818';
const props: PageProps = { sid:'ses_test', profile:{customer_id:'cus_test',display_name:'测试客户',phone_masked:'138 **** 0000',sim_now:'2026-10-28T10:00:00+08:00',risk_profile:{level:null,is_valid:false,valid_until:null}},accounts:[],revision:0,busy:false,notify:vi.fn(),onPropose:vi.fn() };
beforeEach(()=>{
  vi.clearAllMocks();
  localStorage.clear();
  vi.mocked(read).mockResolvedValue({items:[{id:'crd_test',account_id:'acc_test',last4:'0818',status:'ACTIVE',single_limit_cents:500000,daily_limit_cents:2000000,daily_used_cents:0,remaining_daily_cents:2000000,controls:{online:true,overseas:false,atm:true,quickpass:false},current:true}]});
  vi.mocked(getCardNumber).mockResolvedValue({card_id:'crd_test',card_number:pan,synthetic:true});
});
it('loads PAN only on explicit reveal, copies the unformatted number and removes it on close',async()=>{
  const user=userEvent.setup();
  const copy=vi.spyOn(navigator.clipboard,'writeText').mockResolvedValue();
  render(<Cards {...props}/>);
  await screen.findByRole('button',{name:'查看卡号'});
  expect(getCardNumber).not.toHaveBeenCalled();
  expect(screen.queryByLabelText('完整卡号')).toBeNull();
  await user.click(screen.getByRole('button',{name:'查看卡号'}));
  expect(getCardNumber).not.toHaveBeenCalled();
  await user.type(screen.getByLabelText('验证 PIN'), '123456');
  await user.click(screen.getByRole('button',{name:'验证并查看卡号'}));
  expect(await screen.findByLabelText('完整卡号')).toHaveTextContent('6222 0000 0000 0000 818');
  expect(getCardNumber).toHaveBeenCalledWith('ses_test','crd_test','123456');
  expect(screen.getByText('合成演示卡号')).toBeInTheDocument();
  await user.click(screen.getByRole('button',{name:'复制卡号'}));
  expect(copy).toHaveBeenCalledWith(pan);
  expect(screen.getByRole('button',{name:'已复制卡号'})).toBeInTheDocument();
  await user.click(screen.getByRole('button',{name:'关闭并隐藏卡号'}));
  expect(screen.queryByLabelText('完整卡号')).toBeNull();
});
it('does not expose a number from another card',async()=>{
  vi.mocked(getCardNumber).mockResolvedValue({card_id:'crd_other',card_number:pan,synthetic:true});
  render(<Cards {...props}/>);
  await userEvent.click(await screen.findByRole('button',{name:'查看卡号'}));
  await userEvent.type(screen.getByLabelText('验证 PIN'), '123456');
  await userEvent.click(screen.getByRole('button',{name:'验证并查看卡号'}));
  expect(await screen.findByText('卡号信息与当前卡片不一致，请重新查询')).toBeInTheDocument();
  expect(screen.queryByLabelText('完整卡号')).toBeNull();
});
it('ignores a response that arrives after the panel is closed',async()=>{
  let finish!:(value: {card_id:string;card_number:string;synthetic:boolean})=>void;
  vi.mocked(getCardNumber).mockReturnValue(new Promise(resolve=>{finish=resolve}));
  render(<Cards {...props}/>);
  await userEvent.click(await screen.findByRole('button',{name:'查看卡号'}));
  await userEvent.type(screen.getByLabelText('验证 PIN'), '123456');
  await userEvent.click(screen.getByRole('button',{name:'验证并查看卡号'}));
  await userEvent.click(screen.getByRole('button',{name:'关闭并隐藏卡号'}));
  finish({card_id:'crd_test',card_number:pan,synthetic:true});
  await waitFor(()=>expect(screen.queryByRole('dialog')).toBeNull());
  expect(screen.queryByLabelText('完整卡号')).toBeNull();
});
it('shows unavailable errors without fabricating a card number',async()=>{
  vi.mocked(getCardNumber).mockRejectedValue(new Error('当前银行接口仅提供卡号尾号，暂不支持查询完整卡号。'));
  render(<Cards {...props}/>);
  await userEvent.click(await screen.findByRole('button',{name:'查看卡号'}));
  await userEvent.type(screen.getByLabelText('验证 PIN'), '123456');
  await userEvent.click(screen.getByRole('button',{name:'验证并查看卡号'}));
  expect(await screen.findByText('当前银行接口仅提供卡号尾号，暂不支持查询完整卡号。')).toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'复制卡号'})).toBeNull();
});

it('requires a fresh PIN after closing and reopening and keeps errors behind the gate', async () => {
  vi.mocked(getCardNumber).mockRejectedValueOnce(new Error('PIN 不正确，还可尝试 4 次'));
  render(<Cards {...props}/>);
  await userEvent.click(await screen.findByRole('button',{name:'查看卡号'}));
  expect(screen.getByRole('button',{name:'验证并查看卡号'})).toBeDisabled();
  await userEvent.type(screen.getByLabelText('验证 PIN'), '111111');
  await userEvent.click(screen.getByRole('button',{name:'验证并查看卡号'}));
  expect(await screen.findByText('PIN 不正确，还可尝试 4 次')).toBeInTheDocument();
  expect(screen.getByLabelText('验证 PIN')).toHaveValue('');
  expect(screen.queryByLabelText('完整卡号')).toBeNull();
  await userEvent.type(screen.getByLabelText('验证 PIN'), '123456');
  await userEvent.click(screen.getByRole('button',{name:'验证并查看卡号'}));
  await screen.findByLabelText('完整卡号');
  await userEvent.click(screen.getByRole('button',{name:'关闭并隐藏卡号'}));
  await userEvent.click(screen.getByRole('button',{name:'查看卡号'}));
  expect(screen.queryByLabelText('完整卡号')).toBeNull();
  expect(screen.getByLabelText('验证 PIN')).toHaveValue('');
  expect(getCardNumber).toHaveBeenCalledTimes(2);
});
it('selecting an exposed stacked card switches controls and sends actions to that card',async()=>{
 const base={account_id:'acc_test',single_limit_cents:500000,daily_limit_cents:2000000,daily_used_cents:0,remaining_daily_cents:2000000,controls:{online:true,overseas:false,atm:true,quickpass:false},current:true};
 vi.mocked(read).mockResolvedValue({items:[
  {...base,id:'crd_test',last4:'0818',status:'ACTIVE'},
  {...base,id:'crd_second',last4:'6612',status:'FROZEN',single_limit_cents:200000},
  {...base,id:'crd_third',last4:'3906',status:'ACTIVE'},
 ]});
 render(<Cards {...props}/>);
 expect(await screen.findByRole('button',{name:'选择银行卡，尾号6612'})).toBeVisible();
 expect(screen.getByRole('button',{name:'选择银行卡，尾号3906'})).toBeVisible();
 expect(screen.queryByRole('combobox',{name:'选择银行卡'})).toBeNull();
 const front=()=>document.querySelector('.card-deck-front')!;
 const originalMaterial=front().getAttribute('data-card-material');
 const silver=screen.getByRole('button',{name:'选择银行卡，尾号6612'}).getAttribute('data-card-material');
 const gold=screen.getByRole('button',{name:'选择银行卡，尾号3906'}).getAttribute('data-card-material');
 expect(new Set([originalMaterial,silver,gold]).size).toBe(3);
 await userEvent.click(screen.getByRole('button',{name:'选择银行卡，尾号6612'}));
 expect(front()).toHaveAttribute('data-card-material',silver);
 expect(screen.getByRole('button',{name:'选择银行卡，尾号0818'})).toHaveAttribute('data-card-material',originalMaterial);
 expect(screen.getByText('卡片 6612')).toBeInTheDocument();
 expect(screen.getByText('2,000.00')).toBeInTheDocument();
 await userEvent.click(screen.getByRole('button',{name:'解冻卡片'}));
 expect(props.onPropose).toHaveBeenCalledWith('CARD_UNFREEZE',{card_id:'crd_second'});
 expect(screen.getByRole('button',{name:'选择银行卡，尾号0818'})).toBeVisible();
 await userEvent.click(screen.getByRole('button',{name:'查看卡号'}));
 expect(screen.queryByLabelText('完整卡号')).toBeNull();
 await userEvent.click(screen.getByRole('button',{name:'关闭并隐藏卡号'}));
 await userEvent.click(screen.getByRole('button',{name:'选择银行卡，尾号3906'}));
 expect(screen.getByText('卡片 3906')).toBeInTheDocument();
});

it('saves a custom purpose per card, preserves the other card and persists on reopening', async()=>{
 const user=userEvent.setup();
 const base={account_id:'acc_test',single_limit_cents:500000,daily_limit_cents:2000000,daily_used_cents:0,remaining_daily_cents:2000000,controls:{online:true,overseas:false,atm:true,quickpass:false},current:true,status:'ACTIVE'};
 vi.mocked(read).mockResolvedValue({items:[{...base,id:'crd_test',last4:'0818'},{...base,id:'crd_second',last4:'6612'}]});
 const view=render(<Cards {...props}/>);
 await user.click(await screen.findByRole('button',{name:'修改用途'}));
 await user.type(screen.getByRole('textbox',{name:'用途名称'}),'家庭备用金');
 await user.click(screen.getByRole('button',{name:'保存用途'}));
 await user.click(screen.getByRole('button',{name:'选择银行卡，尾号6612'}));
 await user.click(screen.getByRole('button',{name:'修改用途'}));
 expect(screen.getByRole('textbox',{name:'用途名称'})).toHaveValue('');
 await user.click(screen.getByRole('button',{name:'工资收款'}));
 await user.click(screen.getByRole('button',{name:'保存用途'}));
 expect(JSON.parse(localStorage.getItem('safebank:card-purpose:v1:cus_test')!)).toEqual({crd_test:'家庭备用金',crd_second:'工资收款'});
 expect(props.onPropose).not.toHaveBeenCalled();
 view.unmount();
 render(<Cards {...props}/>);
 await user.click(await screen.findByRole('button',{name:'修改用途'}));
 expect(screen.getByRole('textbox',{name:'用途名称'})).toHaveValue('家庭备用金');
 await user.clear(screen.getByRole('textbox',{name:'用途名称'}));
 expect(screen.getByRole('button',{name:'保存用途'})).toBeDisabled();
 await user.type(screen.getByRole('textbox',{name:'用途名称'}),'不要保存');
 await user.click(screen.getByRole('button',{name:'取消'}));
 expect(JSON.parse(localStorage.getItem('safebank:card-purpose:v1:cus_test')!).crd_test).toBe('家庭备用金');
});
it('customer purpose labels stay isolated and malformed saved data does not break cards',async()=>{
 const user=userEvent.setup();
 localStorage.setItem('safebank:card-purpose:v1:cus_test','{broken');
 const {rerender}=render(<Cards {...props}/>);
 await user.click(await screen.findByRole('button',{name:'修改用途'}));
 await user.click(screen.getByRole('button',{name:'生活缴费'}));
 await user.click(screen.getByRole('button',{name:'保存用途'}));
 rerender(<Cards {...props} profile={{...props.profile,customer_id:'cus_other'}}/>);
 await user.click(screen.getByRole('button',{name:'修改用途'}));
 expect(screen.getByRole('textbox',{name:'用途名称'})).toHaveValue('');
});
it('restriction unlock opens the existing step-up flow for the selected card',async()=>{
 const base={account_id:'acc_test',single_limit_cents:500000,daily_limit_cents:2000000,daily_used_cents:0,remaining_daily_cents:2000000,controls:{online:false,overseas:false,atm:true,quickpass:false},current:true};
 vi.mocked(read).mockResolvedValue({items:[{...base,id:'crd_test',last4:'0818',status:'FROZEN'}]});
 render(<Cards {...props}/>);
 const unlock=await screen.findByRole('button',{name:'交易限制 / 解锁'});
 expect(screen.getByRole('heading',{name:'支付限额'}).closest('section')).toContainElement(unlock);
 await userEvent.click(unlock);
 await userEvent.click(screen.getByRole('button',{name:'申请解冻'}));
 expect(props.onPropose).toHaveBeenCalledWith('CARD_UNFREEZE',{card_id:'crd_test'});
 expect(screen.getAllByRole('button',{name:'申请开启'}).every(button=>(button as HTMLButtonElement).disabled)).toBe(true);
 expect(screen.getByText(/不能通过解锁清零/)).toBeInTheDocument();
});
it('synthetic application waits for the refreshed card list before adding the card to the deck',async()=>{
 const user=userEvent.setup();const {rerender}=render(<Cards {...props}/>);
 await user.click(await screen.findByRole('button',{name:'申请新卡'}));
 await user.clear(screen.getByRole('textbox',{name:'新卡用途'}));
 await user.type(screen.getByRole('textbox',{name:'新卡用途'}),'工资收款');
 await user.click(screen.getByRole('button',{name:'预演并确认申请'}));
 expect(props.onPropose).toHaveBeenCalledWith('CARD_APPLY',{purpose:'工资收款'});
 expect(screen.getByRole('dialog',{name:'申请新银行卡'})).toBeInTheDocument();
 const base={account_id:'acc_test',single_limit_cents:500000,daily_limit_cents:2000000,daily_used_cents:0,remaining_daily_cents:2000000,controls:{online:false,overseas:false,atm:true,quickpass:false},current:true,status:'ACTIVE'};
 vi.mocked(read).mockResolvedValue({items:[{...base,id:'crd_test',last4:'0818'},{...base,id:'crd_new',last4:'7004'}]});
 rerender(<Cards {...props} revision={1}/>);
 await screen.findByText('卡片 7004');
 expect(screen.queryByRole('dialog')).toBeNull();
 expect(JSON.parse(localStorage.getItem('safebank:card-purpose:v1:cus_test')!).crd_new).toBe('工资收款');
});

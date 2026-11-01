import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it, vi } from 'vitest';
import { Transfers } from './Banking';
import { read } from './api';
import type { PageProps } from './Banking';
vi.mock('./api',()=>({read:vi.fn()}));
const props: PageProps = {
  sid:'ses_test',profile:{customer_id:'cus_test',display_name:'测试客户',phone_masked:'138 **** 0000',sim_now:'2026-10-29T10:00:00+08:00',risk_profile:{level:null,is_valid:false,valid_until:null}},
  accounts:[
    {id:'acc_one',type:'CURRENT',display_name:'日常活期',account_no_masked:'6222 **** 0818',balance_cents:600000,available_cents:600000,pending_debit_cents:0,pending_credit_cents:0,current_card_id:'crd_one'},
    {id:'acc_two',type:'CURRENT',display_name:'备用活期',account_no_masked:'6222 **** 0928',balance_cents:100000,available_cents:100000,pending_debit_cents:0,pending_credit_cents:0,current_card_id:'crd_two'},
    {id:'acc_savings',type:'SAVINGS',display_name:'安心储蓄',account_no_masked:'6222 **** 0926',balance_cents:2000000,available_cents:2000000,pending_debit_cents:0,pending_credit_cents:0,current_card_id:null},
  ],revision:0,busy:false,notify:vi.fn(),onPropose:vi.fn(),
};
beforeEach(()=>{vi.clearAllMocks();vi.mocked(read).mockResolvedValue({items:[]});});
it('lists every provided funding account without opening a dropdown and permits choosing either card',async()=>{
 render(<Transfers {...props}/>);
 const group=screen.getByRole('group',{name:'付款账户'});
 expect(group).toBeInTheDocument();
 expect(screen.getAllByRole('radio')).toHaveLength(3);
 expect(screen.getByText('6222 **** 0818')).toBeVisible();
 expect(screen.getByText('6222 **** 0928')).toBeVisible();
 expect(screen.getByText('储蓄账户 · 未绑定银行卡')).toBeVisible();
 expect(screen.queryByRole('combobox',{name:'付款账户'})).toBeNull();
 await userEvent.click(screen.getByRole('radio',{name:/备用活期/}));
 expect(screen.getByRole('radio',{name:/备用活期/})).toBeChecked();
 await userEvent.click(screen.getByRole('radio',{name:/日常活期/}));
 expect(screen.getByRole('radio',{name:/日常活期/})).toBeChecked();
 expect(screen.getByRole('radio',{name:/备用活期/})).not.toBeChecked();
 await userEvent.click(screen.getByRole('tab',{name:'定时转账'}));
 expect(screen.getAllByRole('radio')).toHaveLength(3);
 expect(screen.getByRole('radio',{name:/日常活期/})).toBeChecked();
});
it('shows an empty account state without creating fictional card options',async()=>{
 await act(async()=>{render(<Transfers {...props} accounts={[]}/>);});
 expect(screen.queryByRole('radio')).toBeNull();
 expect(screen.getByText('暂无可用账户')).toBeVisible();
});
it('adds a payee from the transfer form and only selects it after an authoritative list refresh', async()=>{
 const user=userEvent.setup();
 const {rerender}=render(<Transfers {...props}/>);
 await user.click(screen.getByRole('radio',{name:/备用活期/}));
 await user.type(screen.getByRole('textbox',{name:'转账金额'}),'88.50');
 await user.click(screen.getByRole('button',{name:'添加新收款人'}));
 await user.type(screen.getByRole('textbox',{name:'收款人姓名'}),'新客户');
 await user.type(screen.getByRole('textbox',{name:'银行卡号'}),'6222000000000001234');
 await user.type(screen.getByRole('textbox',{name:'开户行'}),'中国工商银行');
 await user.click(screen.getByRole('button',{name:'预演并确认添加'}));
 expect(props.onPropose).toHaveBeenCalledWith('PAYEE_ADD',{
  name:'新客户',account_no:'6222000000000001234',bank_name:'中国工商银行',phone:null,alias:null,
 });
 expect(screen.getByRole('dialog',{name:'添加新收款人'})).toBeInTheDocument();
 vi.mocked(read).mockResolvedValue({items:[{id:'pye_new',name:'新客户',bank_name:'中国工商银行',account_no_masked:'**** 1234',has_transfer_history:false,alias:null}]});
 rerender(<Transfers {...props} revision={1}/>);
 await screen.findByRole('button',{name:'收款目标'});
 await screen.findByText('新客户',{selector:'.choice-trigger strong'});
 expect(screen.queryByRole('dialog')).toBeNull();
 expect(screen.getByRole('radio',{name:/备用活期/})).toBeChecked();
 expect(screen.getByRole('textbox',{name:'转账金额'})).toHaveValue('88.50');
 await user.click(screen.getByRole('button',{name:'查看预演'}));
 expect(props.onPropose).toHaveBeenCalledWith('TRANSFER_EXTERNAL',expect.objectContaining({from_account_id:'acc_two',payee_id:'pye_new',amount_cents:8850}));
});
it('the payee picker offers adding a recipient even when there is no saved payee', async()=>{
 const user=userEvent.setup();
 render(<Transfers {...props}/>);
 await user.click(screen.getByRole('button',{name:'收款目标'}));
 await user.click(screen.getByRole('option',{name:/添加新收款人/}));
 expect(screen.getByRole('dialog',{name:'添加新收款人'})).toBeInTheDocument();
 await user.type(screen.getByRole('textbox',{name:'收款人姓名'}),'测试');
 await user.type(screen.getByRole('textbox',{name:'银行卡号'}),'1234');
 await user.type(screen.getByRole('textbox',{name:'开户行'}),'测试银行');
 await user.click(screen.getByRole('button',{name:'预演并确认添加'}));
 expect(screen.getByText('请填写姓名、12–32 位银行卡号和开户行。')).toBeInTheDocument();
 expect(props.onPropose).not.toHaveBeenCalled();
 await user.click(screen.getByRole('button',{name:'返回转账'}));
 expect(screen.queryByRole('dialog')).toBeNull();
});
it.each(['13800000088','１３８００００００８８'])('saves a manually entered optional phone on a new payee: %s',async(phone)=>{
 const user=userEvent.setup();render(<Transfers {...props}/>);
 await user.click(screen.getByRole('button',{name:'添加新收款人'}));
 await user.type(screen.getByRole('textbox',{name:'收款人姓名'}),'新客户');
 await user.type(screen.getByRole('textbox',{name:'银行卡号'}),'6222000000000001234');
 await user.type(screen.getByRole('textbox',{name:'开户行'}),'中国工商银行');
 await user.type(screen.getByRole('textbox',{name:'手机号（选填）'}),phone);
 await user.click(screen.getByRole('button',{name:'预演并确认添加'}));
 expect(props.onPropose).toHaveBeenCalledWith('PAYEE_ADD',expect.objectContaining({phone:'13800000088'}));
 expect(props.onPropose).toHaveBeenCalledTimes(1);
});
it('rejects a partial optional phone without proposing a payee',async()=>{
 const user=userEvent.setup();render(<Transfers {...props}/>);
 await user.click(screen.getByRole('button',{name:'添加新收款人'}));
 await user.type(screen.getByRole('textbox',{name:'收款人姓名'}),'新客户');
 await user.type(screen.getByRole('textbox',{name:'银行卡号'}),'6222000000000001234');
 await user.type(screen.getByRole('textbox',{name:'开户行'}),'中国工商银行');
 await user.type(screen.getByRole('textbox',{name:'手机号（选填）'}),'1380000');
 await user.click(screen.getByRole('button',{name:'预演并确认添加'}));
 expect(screen.getByText('手机号请填写完整的11位数字，或留空。')).toBeVisible();
 expect(props.onPropose).not.toHaveBeenCalled();
});
it('uses the same manually entered phone in recipient management without preselecting a transfer',async()=>{
 const user=userEvent.setup();render(<Transfers {...props}/>);
 await user.click(screen.getByRole('tab',{name:'收款人'}));
 await user.type(screen.getByRole('textbox',{name:'收款人姓名'}),'新客户');
 await user.type(screen.getByRole('textbox',{name:'银行卡号'}),'6222000000000001234');
 await user.type(screen.getByRole('textbox',{name:'开户行'}),'中国工商银行');
 await user.type(screen.getByRole('textbox',{name:'手机号（选填）'}),'13800000088');
 await user.click(screen.getByRole('button',{name:'查看预演'}));
 expect(props.onPropose).toHaveBeenCalledWith('PAYEE_ADD',expect.objectContaining({phone:'13800000088'}));
 expect(props.onPropose).not.toHaveBeenCalledWith('TRANSFER_EXTERNAL',expect.anything());
});

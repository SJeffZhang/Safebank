import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it, vi } from 'vitest';
import { Wealth } from './Services';
import { read } from './api';
import type { PageProps } from './Banking';
vi.mock('./api',()=>({read:vi.fn()}));
const props:PageProps={sid:'ses_test',profile:{customer_id:'cus_test',display_name:'测试',phone_masked:'138 **** 0000',sim_now:'2026-10-29T10:00:00+08:00',risk_profile:{level:2,is_valid:true,valid_until:'2027-10-29T00:00:00+08:00'}},accounts:[{id:'acc_test',type:'CURRENT',display_name:'活期',account_no_masked:'**** 0818',balance_cents:100000,available_cents:100000,pending_debit_cents:0,pending_credit_cents:0,current_card_id:'crd_test'}],revision:0,busy:false,notify:vi.fn(),onPropose:vi.fn()};
const base={product_type:'MONEY_MARKET',risk_label:'保守',nav:'1.0000',nav_date:'2026-10-28',recent_return_bp:12,confirm_days:0,settle_days:1,min_amount_cents:100,risk_disclosure:'不保证本金及收益。',disclosure_version:'v1',nav_history:[]};
beforeEach(()=>{vi.clearAllMocks();vi.mocked(read).mockImplementation(async(_sid,tool)=>tool==='list_products'?{items:[{...base,id:'prd_low',name:'低风险示例',risk_level:1},{...base,id:'prd_high',name:'高风险示例',risk_level:4}]}:tool==='get_holdings'?{holdings:[{account_id:'acc_test',product_id:'prd_low',units:'10.0000',available_units:'10.0000',market_value_cents:1000,reserved_receivable_cents:0}],orders:[]}:{questionnaire_id:'rq_test',questions:[]});});
it('explains suitability and filters high risk products without treating a UI match as authorization',async()=>{
 const user=userEvent.setup();render(<Wealth {...props}/>);
 const high=(await screen.findByText('高风险示例')).closest('article')!;
 expect(within(high).getByRole('button',{name:'超出风险等级'})).toBeDisabled();
 await user.click(within(high).getByRole('button',{name:'风险评估'}));
 expect(screen.getByRole('dialog',{name:'产品风险评估'})).toHaveTextContent('超出当前承受等级');
 await user.click(screen.getByRole('button',{name:'关闭弹窗'}));
 await user.click(screen.getByRole('button',{name:'风险匹配'}));
 expect(screen.queryByText('高风险示例')).toBeNull();expect(screen.getByText('低风险示例')).toBeInTheDocument();
});
it('quick subscription still requires a user amount and submits cents to the existing proposal flow',async()=>{
 const user=userEvent.setup();render(<Wealth {...props}/>);
 await user.click(await screen.findByRole('button',{name:'一键申购'}));
 expect(props.onPropose).not.toHaveBeenCalled();
 await user.type(screen.getByRole('textbox',{name:'申购金额（元）'}),'12.34');
 await user.click(screen.getByRole('button',{name:'查看预演'}));
 expect(props.onPropose).toHaveBeenCalledWith('FUND_SUBSCRIBE',{account_id:'acc_test',product_id:'prd_low',amount_cents:1234});
});
it('quick redemption fills the holding account and exact available units but waits for preview',async()=>{
 const user=userEvent.setup();render(<Wealth {...props}/>);
 await user.click(await screen.findByRole('button',{name:'一键赎回 · 我的持仓'}));
 await user.click(screen.getByRole('button',{name:'一键赎回'}));
 const input=screen.getByRole('textbox',{name:'赎回份额（最多四位小数）'});
 expect(input).toHaveValue('10.0000');expect(props.onPropose).not.toHaveBeenCalled();
 await user.clear(input);await user.type(input,'11');
 await user.click(screen.getByRole('button',{name:'查看预演'}));
 expect(screen.getByText('赎回份额超过该账户的可用持仓。')).toBeInTheDocument();
 expect(props.onPropose).not.toHaveBeenCalled();
 await user.click(screen.getByRole('button',{name:'全部赎回'}));
 await user.click(screen.getByRole('button',{name:'查看预演'}));
 expect(props.onPropose).toHaveBeenCalledWith('FUND_REDEEM',{account_id:'acc_test',product_id:'prd_low',units:'10.0000'});
});
it('an expired assessment prevents purchase and provides a questionnaire entry',async()=>{
 render(<Wealth {...props} profile={{...props.profile,risk_profile:{level:2,is_valid:false,valid_until:'2025-10-29T00:00:00+08:00'}}}/>);
 expect(await screen.findByRole('button',{name:'开始风险测评'})).toBeInTheDocument();
 expect(screen.getAllByRole('button',{name:'需先测评'}).every(button=>(button as HTMLButtonElement).disabled)).toBe(true);
});

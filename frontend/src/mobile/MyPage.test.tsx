import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it, vi } from 'vitest';
import { Security } from './Security';
import { read, request } from './api';
import type { PageProps } from './Banking';
vi.mock('./api',()=>({read:vi.fn(),request:vi.fn(),getPlan:vi.fn()}));
const props:PageProps & {openApproval:(id:string)=>void}={sid:'ses_test',profile:{customer_id:'cus_test',display_name:'测试客户',phone_masked:'138 **** 0000',sim_now:'2026-10-29T10:00:00+08:00',risk_profile:{level:2,is_valid:true,valid_until:'2027-10-29T00:00:00+08:00'}},accounts:[],revision:0,busy:false,notify:vi.fn(),onPropose:vi.fn(),openApproval:vi.fn()};
beforeEach(()=>{
 vi.clearAllMocks();history.replaceState(null,'','#security');
 vi.mocked(request).mockResolvedValue({items:[]});
 vi.mocked(read).mockImplementation(async(_sid,tool)=>tool==='list_subscriptions'?{
  items:[{instruction_id:'si_test',account_id:'acc_test',merchant_name:'演示会员',period:'MONTHLY',amount_cents:1800,annual_cost_cents:21600,next_due_at:'2026-10-30T06:00:00+08:00',status:'ACTIVE'}],total_monthly_cents:1800,total_annual_cents:21600,
 }:tool==='upcoming_charges'?{items:[],reserve_total_cents:0}:{items:[]});
});
it('replaces the security heading with the customer identity and embeds working subscription management by default',async()=>{
 const user=userEvent.setup();render(<Security {...props}/>);
 expect(screen.getByRole('heading',{level:1,name:'测试客户'})).toBeInTheDocument();
 expect(screen.getByRole('img',{name:'测试客户的头像'})).toHaveTextContent('测');
 expect(screen.queryByRole('heading',{name:'安全中心'})).toBeNull();
 expect(await screen.findByText('演示会员')).toBeInTheDocument();
 expect(screen.queryByRole('heading',{level:1,name:'订阅管理'})).toBeNull();
 await user.click(screen.getByRole('button',{name:'管理授权'}));
 expect(props.onPropose).toHaveBeenCalledWith('MANDATE_CANCEL',{instruction_id:'si_test'});
 await user.click(screen.getByRole('tab',{name:'安全记录'}));
 expect(await screen.findByRole('heading',{name:'待确认计划'})).toBeInTheDocument();
 expect(screen.queryByText('演示会员')).toBeNull();
});
it('a notification deep link opens notifications and can return to subscriptions in My',async()=>{
 history.replaceState(null,'','#security?tab=notifications');
 const user=userEvent.setup();render(<Security {...props}/>);
 expect(await screen.findByRole('heading',{name:'消息通知'})).toBeInTheDocument();
 await user.click(screen.getByRole('tab',{name:'订阅管理'}));
 expect(await screen.findByText('演示会员')).toBeInTheDocument();
 expect(location.hash).toBe('#security');
});

import { render,screen,within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {beforeEach,expect,it,vi} from 'vitest';
import {Assistant} from './Assistant';
import {request,stream} from './api';
import type {PageProps} from './Banking';
import type {UICard} from './types';
vi.mock('./api',()=>({isPreview:true,request:vi.fn(),stream:vi.fn(),read:vi.fn(),getPlan:vi.fn()}));
const props={sid:'ses_wire',profile:{sim_now:'2026-10-15T00:00:00+08:00'} as PageProps['profile'],accounts:[],revision:0,busy:false,notify:vi.fn(),onPropose:vi.fn(),openApproval:vi.fn(),onNewSession:vi.fn(),onResumeRegister:vi.fn()};
beforeEach(()=>{vi.clearAllMocks();Element.prototype.scrollIntoView=vi.fn();vi.mocked(request).mockResolvedValue({clarification:null});});
async function show(type:string,data:unknown){
 const card:UICard={id:'wire_card',type,source_ref:'src_wire',data,actions:[]};
 vi.mocked(stream).mockImplementation(async(_sid,_body,handle)=>handle({event:'custom',data:{type:'cards',cards:[card]}}));
 const user=userEvent.setup();render(<Assistant {...props}/>);
 await user.type(screen.getByRole('textbox'),'查询业务详情');await user.click(screen.getByRole('button',{name:'发送消息'}));
 return document.querySelector<HTMLElement>('.safe-ui-card')!;
}
it('renders a get_product direct ProductView without inventing an items wrapper',async()=>{
 const card=await show('PRODUCTS',{id:'prd_mmf',product_type:'MMF',risk_level:1,risk_label:'保守',nav:'1.0000',nav_date:'2026-10-15',recent_return_bp:12,confirm_days:0,settle_days:0,min_amount_cents:100,risk_disclosure:'不保证本金及收益',disclosure_version:'v1',nav_history:[]});
 expect(card).toHaveTextContent('理财产品（名称暂不可用）');expect(card).not.toHaveTextContent('prd_mmf');expect(card).toHaveTextContent('R1');expect(card).not.toHaveTextContent('当前没有可展示的记录');
});
it('renders nested anomaly transaction amounts and rule codes from the actual wire shape',async()=>{
 const card=await show('ANOMALIES',{items:[{transaction:{id:'tx_suspicious',account_id:'acc_a',direction:'OUT',kind:'CARD_PAYMENT',status:'POSTED',amount_cents:99000,counterparty:'可疑商户',memo:null,category:'购物',occurred_at:'2026-09-12T02:00:00+08:00',card_id:'crd_a'},codes:['ANOMALY_NIGHT_LARGE'],evidence:{baseline_count:5,mean_cents:1000,threshold_cents:10000,previous_transaction_id:null,window_minutes:null}}]});
 expect(card).toHaveTextContent('990.00');expect(card).toHaveTextContent('深夜');expect(card).not.toHaveTextContent('业务记录—');
});
it('shows typical subscription charges from typical_amount_cents without misreporting a zero or dash',async()=>{
 const card=await show('SUBSCRIPTIONS',{items:[{merchant_id:'mch_music',instruction_id:'ins_music',billing_id:'bil_music',period:'MONTHLY',typical_amount_cents:3000,occurrences:4,last_charged_at:'2026-09-10T00:00:00+08:00',is_idle:true,usage_evidence:{recent_usage_count:0,previous_usage_count:0},annual_cost_cents:36000}]});
 expect(card).toHaveTextContent('30.00');expect(card).toHaveTextContent('典型扣费');
});
it('includes scheduled charges in an upcoming card even when there are no merchant charges',async()=>{
 const card=await show('SUBSCRIPTIONS',{items:[],scheduled_items:[{instruction_id:'ins_save',account_id:'acc_a',target_display:'储蓄账户',due_at:'2026-10-16T00:00:00+08:00',amount_cents:10000}],reserve_total_cents:10000,charges_total_cents:0,scheduled_transfers_total_cents:10000,by_account:[]});
 expect(card).toHaveTextContent('储蓄账户');expect(card).toHaveTextContent('100.00');expect(card).not.toHaveTextContent('当前没有可展示的记录');
});
it('shows pending fund orders without treating them as owned holdings or available money',async()=>{
 const card=await show('HOLDINGS',{holdings:[],orders:[{id:'ord_pending',account_id:'acc_a',product_id:'prd_bond',type:'SUB',status:'PENDING_CONFIRM',amount_cents:25000,units:'0.0000',nav:null,trade_date:'2026-10-15',confirm_at:'2026-10-16T00:00:00+08:00',settle_at:null,failure_code:null}]});
 expect(card).toHaveTextContent('暂无持仓');expect(card).toHaveTextContent('待确认');expect(card).toHaveTextContent('250.00');expect(card).not.toHaveTextContent('当前没有可展示的记录');
 expect(props.onPropose).not.toHaveBeenCalled();expect(within(card).queryByRole('button',{name:/申购|赎回/})).toBeNull();
});

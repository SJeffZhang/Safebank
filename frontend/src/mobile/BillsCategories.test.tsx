import { render, screen, within } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { Bills } from './Banking';
import { read } from './api';
import type { PageProps } from './Banking';
vi.mock('./api',()=>({read:vi.fn()}));
const props: PageProps={sid:'ses_test',profile:{customer_id:'cus_test',display_name:'测试',phone_masked:'138 **** 0000',sim_now:'2026-10-29T10:00:00+08:00',risk_profile:{level:null,is_valid:false,valid_until:null}},accounts:[],revision:0,busy:false,notify:vi.fn(),onPropose:vi.fn()};
function mockSummary(other:boolean) {
 vi.mocked(read).mockImplementation(async(_sid,tool)=> tool==='get_spending_summary' ? {
  month:'2026-10',total_cents:other?11000:10000,previous_total_cents:0,mom_change_cents:10000,mom_change_bp:null,transfer_out_cents:0,daily:[],
  categories:[{category:'餐饮',amount_cents:10000,share_bp:other?9091:10000,previous_amount_cents:0},...(other?[{category:'其他',amount_cents:1000,share_bp:909,previous_amount_cents:0}]:[])],
 } : {items:[],income_cents:0,expense_cents:0,next_cursor:null});
}
beforeEach(()=>vi.clearAllMocks());
it('always shows Other with zero amount and no bar when the summary has no other spending',async()=>{
 mockSummary(false);render(<Bills {...props}/>);
 const panel=(await screen.findByRole('heading',{name:'消费分布'})).closest('section')!;
 const row=within(panel).getByText('其他').closest('.category-row')!;
 expect(row).toHaveTextContent('0.0%');expect(row).toHaveTextContent('¥ 0.00');
 expect(row.querySelector('.bar-track > div')).toHaveStyle({width:'0%'});
 expect(within(panel).getByText('餐饮').closest('.category-row')).toHaveTextContent('¥ 100.00');
});
it('uses the authoritative Other amount and share once instead of replacing it with a zero placeholder',async()=>{
 mockSummary(true);render(<Bills {...props}/>);
 const panel=(await screen.findByRole('heading',{name:'消费分布'})).closest('section')!;
 expect(within(panel).getAllByText('其他')).toHaveLength(1);
 const row=within(panel).getByText('其他').closest('.category-row')!;
 expect(row).toHaveTextContent('9.1%');expect(row).toHaveTextContent('¥ 10.00');
});

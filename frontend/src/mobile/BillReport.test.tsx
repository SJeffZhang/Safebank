import { beforeEach, expect, it, vi } from 'vitest';
import { loadBillReport, reportMonths } from './BillReport';
import { read } from './api';
vi.mock('./api',()=>({read:vi.fn()}));
beforeEach(()=>{vi.clearAllMocks();vi.mocked(read).mockImplementation(async(_sid,tool,args)=>tool==='get_spending_summary'?{
 month:args?.month,total_cents:101,transfer_out_cents:20,categories:[{category:'餐饮',amount_cents:101}],daily:[],
}:{income_cents:1000,expense_cents:121});});
it('current-year reports stop at the simulated month and prior years cover twelve months',()=>{
 expect(reportMonths('year','2026-10','2026-10')).toHaveLength(10);
 expect(reportMonths('year','2025-10','2026-10')).toHaveLength(12);
 expect(reportMonths('month','2026-11','2026-10')).toEqual([]);
});
it('sums integer cents and transaction aggregates without relying on page items',async()=>{
 const result=await loadBillReport('ses_test',['2026-09','2026-10']);
 expect(result).toMatchObject({consumption:202,income:2000,expense:242,transfers:40,missing:[]});
 expect(result.categories).toContainEqual(['餐饮',202]);
 expect(result.categories).toContainEqual(['其他',0]);
 expect(read).toHaveBeenCalledWith('ses_test','get_transactions',expect.objectContaining({from_at:'2026-10-01T00:00:00+08:00',to_at:'2026-11-01T00:00:00+08:00',limit:1}));
});
it('failed months are explicitly excluded instead of fabricated as zero months',async()=>{
 vi.mocked(read).mockImplementation(async(_sid,tool,args)=>{
  if(args?.month==='2026-09')throw new Error('unavailable');
  return tool==='get_spending_summary'?{month:args?.month,total_cents:500,transfer_out_cents:0,categories:[],daily:[]}:{income_cents:1000,expense_cents:500};
 });
 const result=await loadBillReport('ses_test',['2026-09','2026-10']);
 expect(result.rows).toHaveLength(1);expect(result.missing).toEqual(['2026-09']);expect(result.consumption).toBe(500);
});

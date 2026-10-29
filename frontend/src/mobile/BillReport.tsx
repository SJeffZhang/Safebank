import type { Summary } from './types';
import { read } from './api';
import { useLoad } from './hooks';
import { Alert, Button, Loading, Money, money } from './ui';
export function reportMonths(period: 'month' | 'year', month: string, simMonth: string) {
  const candidates = period === 'month' ? [month] : Array.from({length:12},(_,i)=>`${month.slice(0,4)}-${String(i+1).padStart(2,'0')}`);
  return candidates.filter(value => value <= simMonth);
}
export async function loadBillReport(sid: string, months: string[]) {
  const rows: {month: string; summary: Summary; income_cents: number; expense_cents: number}[]=[];
  const missing: string[]=[];
  // Bound the number of simultaneous read requests, including annual reports.
  for(let offset=0;offset<months.length;offset+=3) {
    await Promise.all(months.slice(offset,offset+3).map(async month=>{
      const [year,index]=month.split('-').map(Number);
      const next=index===12?`${year+1}-01`:`${year}-${String(index+1).padStart(2,'0')}`;
      try {
        const [summary, totals]=await Promise.all([
          read<Summary>(sid,'get_spending_summary',{month,account_id:null}),
          read<{income_cents:number;expense_cents:number}>(sid,'get_transactions',{
            account_id:null,from_at:`${month}-01T00:00:00+08:00`,to_at:`${next}-01T00:00:00+08:00`,
            category:null,merchant_id:null,kind:null,cursor:null,limit:1,
          }),
        ]);
        rows.push({month,summary,...totals});
      } catch { missing.push(month); }
    }));
  }
  rows.sort((a,b)=>a.month.localeCompare(b.month));
  const categories=new Map<string,number>();
  let consumption=0,income=0,expense=0,transfers=0;
  for(const row of rows) {
    consumption+=row.summary.total_cents;income+=row.income_cents;expense+=row.expense_cents;transfers+=row.summary.transfer_out_cents;
    for(const category of row.summary.categories) categories.set(category.category,(categories.get(category.category)??0)+category.amount_cents);
  }
  if(!categories.has('其他'))categories.set('其他',0);
  return {rows,missing:missing.sort(),consumption,income,expense,transfers,categories:[...categories].sort((a,b)=>b[1]-a[1])};
}
export function BillReport({sid,month,period,simMonth}:{sid:string;month:string;period:'month'|'year';simMonth:string}) {
  const months=reportMonths(period,month,simMonth);
  const q=useLoad(()=>loadBillReport(sid,months),[sid,month,period,simMonth]);
  const title=period==='month'?`${month} 月度账单报告`:`${month.slice(0,4)} 年度账单报告`;
  if(q.loading)return <div className="modal-body"><Loading/></div>;
  if(q.error||!q.value?.rows.length)return <div className="modal-body"><Alert tone="error">{months.length?'账单报告暂不可用，请重试。':'该期间尚未发生，暂无可生成的账单报告。'}</Alert>{months.length>0&&<Button onClick={q.reload}>重新生成</Button>}</div>;
  const report=q.value;
  function download() {
    const text=[title,`统计范围：${months[0]} 至 ${months[months.length - 1]}`,report.missing.length?`不完整：缺少 ${report.missing.join('、')}`:'统计数据已读取',
      `入账总额：¥${money(report.income)}`,`支出总额：¥${money(report.expense)}`,`其中消费：¥${money(report.consumption)}`,`其中对外转出：¥${money(report.transfers)}`,
      '消费分类',...report.categories.map(([name,value])=>`${name}：¥${money(value)}`),
      '逐月汇总（入账 / 消费 / 支出）',...report.rows.map(row=>`${row.month}：¥${money(row.income_cents)} / ¥${money(row.summary.total_cents)} / ¥${money(row.expense_cents)}`),
      '按银行模拟时间统计；入账与支出包含转账，消费单独列示。'].join('\n');
    const url=URL.createObjectURL(new Blob([text],{type:'text/plain;charset=utf-8'}));
    const anchor=document.createElement('a');anchor.href=url;anchor.download=`SafeBank-${period==='month'?month:month.slice(0,4)}-账单报告.txt`;anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  return <div className="modal-body bill-report">
    <div><span className="eyebrow">YOUR BILL REPORT</span><h3>{title}</h3><p className="helper">{months[0]} 至 {months[months.length - 1]} · 全部账户 · 已读取 {report.rows.length}/{months.length} 个月</p></div>
    {report.missing.length>0&&<Alert tone="amber">缺少 {report.missing.join('、')} 的数据，以下合计仅包含已成功读取的月份。</Alert>}
    <div className="report-totals">{[['入账总额',report.income],['支出总额',report.expense],['其中消费',report.consumption],['对外转出',report.transfers]].map(([label,value])=><div key={String(label)}><span>{label}</span><Money value={Number(value)}/></div>)}</div>
    <p className="helper">入账和支出包含转账，消费单独列示。年度报告仅统计截至银行模拟月份的数据。</p>
    <section><h4>消费分类汇总</h4>{report.categories.map(([label,value])=><div className="report-data-row" key={label}><strong>{label}</strong><span>{report.consumption?((value/report.consumption)*100).toFixed(1):'0.0'}%</span><Money value={value}/></div>)}</section>
    {period==='year'&&<section><h4>逐月消费</h4>{report.rows.map(row=><div className="report-data-row" key={row.month}><strong>{row.month}</strong><span>消费</span><Money value={row.summary.total_cents}/></div>)}</section>}
    <Button variant="secondary" onClick={download}>下载文字报告</Button>
  </div>;
}

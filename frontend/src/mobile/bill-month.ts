/** Display-only month selection. It never grants or changes write authority. */
function singleBillMonth(text: string, simNow: string): string | null {
  const time = new Date(simNow);
  if (!Number.isFinite(time.getTime())) return null;
  const parts = new Intl.DateTimeFormat('en-CA', {timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit'}).formatToParts(time);
  let year = Number(parts.find(p => p.type === 'year')?.value);
  let month = Number(parts.find(p => p.type === 'month')?.value);
  if (/去年|前年|明年|季度|年度|年报|中秋|春节|前[一二两三四五六七八九十\d]+个?月/.test(text)) return null;
  const explicit = text.match(/(?:(\d{4})\s*年\s*)?(\d+|[零一二三四五六七八九十百]+)\s*月/);
  const iso = text.match(/(\d{4})-(\d{2})(?!\d)/);
  if (iso) { year = Number(iso[1]); month = Number(iso[2]); }
  else if (explicit) {
    year = Number(explicit[1] ?? year);
    month = /^\d+$/.test(explicit[2]) ? Number(explicit[2]) : ['','一','二','三','四','五','六','七','八','九','十','十一','十二'].indexOf(explicit[2]);
  } else if (/上个?月/.test(text)) {
    if (--month === 0) { month = 12; year--; }
  }
  if (month < 1 || month > 12) return null;
  return `${year}-${String(month).padStart(2,'0')}`;
}
/** Preserve a requested comparison as separate source months, never a merged total. */
export function billMonths(text: string, simNow: string): string[] | null {
  const previousComparison = /(?:和|与)前一个月比/.test(text);
  const month = singleBillMonth(previousComparison ? text.replace("前一个月", "") : text, simNow);
  if (!month) return null;
  if (!previousComparison) return [month];
  const [year, index] = month.split("-").map(Number);
  const previous = index === 1 ? `${year - 1}-12` : `${year}-${String(index - 1).padStart(2, "0")}`;
  return [month, previous];
}
export function billMonth(text: string, simNow: string): string | null {
  return billMonths(text, simNow)?.[0] ?? null;
}
export function monthRange(month: string) {
  const [year, index] = month.split('-').map(Number);
  const next = index === 12 ? `${year+1}-01` : `${year}-${String(index+1).padStart(2,'0')}`;
  return {from_at:`${month}-01T00:00:00+08:00`,to_at:`${next}-01T00:00:00+08:00`,account_id:null};
}

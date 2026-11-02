import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { Assistant } from './Assistant';
import { Approval } from './Approval';
import { getPlan, getView, request, stream } from './api';
import type { PageProps } from './Banking';
import type { PlanView } from './types';
vi.mock('./api',()=>({isPreview:true,getPlan:vi.fn(),getView:vi.fn(),request:vi.fn(),stream:vi.fn(),read:vi.fn()}));
const props={sid:'ses_lifecycle',profile:{sim_now:'2026-10-15T00:00:00+08:00'} as PageProps['profile'],accounts:[],revision:0,busy:false,notify:vi.fn(),onPropose:vi.fn(),openApproval:vi.fn(),onNewSession:vi.fn(),onResumeRegister:vi.fn()};
const panel=(status='USED'):PlanView=>({approval_id:'apv_a',plan_id:'pln_a',session_id:'ses_lifecycle',evaluation_id:'evl_a',status,origin:'agent',risk:'YELLOW',steps:[],preflight:null,findings:[],disclosures:[],risk_ack_required:false,otp_required:false,expires_at_wall:'2099-01-01T00:00:00Z',view_hash:'bound-hash'});
beforeEach(()=>{vi.clearAllMocks();Element.prototype.scrollIntoView=vi.fn();vi.mocked(request).mockResolvedValue({clarification:null});vi.mocked(getView).mockResolvedValue(panel());});
afterEach(()=>vi.useRealTimers());
function emitPending(){vi.mocked(stream).mockImplementation(async(_sid,_body,handle)=>{handle({event:'custom',data:{type:'cards',cards:[{id:'card_a',type:'PLAN_STATUS',data:{plan_id:'pln_a',status:'PENDING_APPROVAL'},actions:[{kind:'OPEN_APPROVAL',ref:'apv_a'}]}]}});});}
it.each([['EXECUTED','操作已完成'],['FAILED','执行失败'],['REJECTED','已拒绝'],['EXPIRED','授权已过期'],['UNKNOWN','结果确认中']])('refreshes an old pending chat card from its own actual %s plan',async(status,label)=>{
 emitPending();vi.mocked(getPlan).mockResolvedValue({plan_id:'pln_a',status,decision:null});
 const user=userEvent.setup();render(<Assistant {...props}/>);
 await user.type(screen.getByRole('textbox'),'冻结银行卡');await user.click(screen.getByRole('button',{name:'发送消息'}));
 expect(await screen.findByText(label)).toBeInTheDocument();
 expect(screen.queryByText('等待您确认')).toBeNull();
 expect(screen.queryByRole('button',{name:'查看确认面板'})).toBeNull();
 expect(getPlan).toHaveBeenCalledWith('pln_a');expect(request).not.toHaveBeenCalledWith(expect.stringContaining('/confirm'),expect.anything());
});
it('does not borrow success from another plan or a failed fetch',async()=>{
 emitPending();vi.mocked(getPlan).mockResolvedValue({plan_id:'pln_other',status:'EXECUTED',decision:null});
 const user=userEvent.setup();const view=render(<Assistant {...props}/>);
 await user.type(screen.getByRole('textbox'),'冻结银行卡');await user.click(screen.getByRole('button',{name:'发送消息'}));
 expect(await screen.findByText('状态暂不可用')).toBeInTheDocument();expect(screen.queryByText('操作已完成')).toBeNull();
 vi.mocked(getPlan).mockRejectedValue(new Error('读取失败'));view.rerender(<Assistant {...props} revision={1}/>);
 expect(await screen.findByText('读取失败')).toBeInTheDocument();expect(screen.queryByText('操作已完成')).toBeNull();
});
it.each([['EXECUTED','操作已完成'],['FAILED','执行失败'],['REJECTED','已拒绝'],['EXPIRED','授权已过期']])('a reopened used approval reflects %s in its header without a pending label',async(status,label)=>{
 vi.mocked(getPlan).mockResolvedValue({plan_id:'pln_a',status,decision:null});
 const complete=vi.fn();render(<Approval id='apv_a' onClose={vi.fn()} onComplete={complete} onDecision={vi.fn()} onResume={vi.fn()}/>);
 const modal=await screen.findByRole('dialog');await waitFor(()=>expect(within(modal).getAllByText(label).length).toBeGreaterThan(0));
 expect(within(modal).queryByText('待您确认')).toBeNull();expect(within(modal).queryByText('请确认这份操作计划')).toBeNull();
 expect(request).not.toHaveBeenCalled();expect(complete).toHaveBeenCalledTimes(status==='EXECUTED'?1:0);
});
it('polls UNKNOWN using the original plan and refreshes only after EXECUTED',async()=>{
 vi.useFakeTimers();vi.mocked(getPlan).mockResolvedValueOnce({plan_id:'pln_a',status:'UNKNOWN',decision:null}).mockResolvedValue({plan_id:'pln_a',status:'EXECUTED',decision:null});
 const complete=vi.fn();render(<Approval id='apv_a' onClose={vi.fn()} onComplete={complete} onDecision={vi.fn()} onResume={vi.fn()}/>);
 await act(async()=>{await vi.advanceTimersByTimeAsync(0)});
 expect(screen.getAllByText('结果确认中').length).toBeGreaterThan(0);expect(complete).not.toHaveBeenCalled();expect(request).not.toHaveBeenCalled();
 await act(async()=>{await vi.advanceTimersByTimeAsync(1800)});
 expect(screen.getAllByText('操作已完成').length).toBeGreaterThan(0);expect(complete).toHaveBeenCalledOnce();expect(getPlan).toHaveBeenNthCalledWith(2,'pln_a');expect(request).not.toHaveBeenCalled();
});

it('refreshes business lists once for a green agent execution, including after later revisions',async()=>{
 emitPending();vi.mocked(getPlan).mockResolvedValue({plan_id:'pln_a',status:'EXECUTED',decision:null});
 const onExecuted=vi.fn();const user=userEvent.setup();const view=render(<Assistant {...props} {...{onExecuted}}/>);
 await user.type(screen.getByRole('textbox'),'冻结银行卡');await user.click(screen.getByRole('button',{name:'发送消息'}));
 await screen.findByText('操作已完成');expect(onExecuted).toHaveBeenCalledOnce();
 view.rerender(<Assistant {...props} revision={1} {...{onExecuted}}/>);
 await waitFor(()=>expect(getPlan).toHaveBeenCalledTimes(2));expect(onExecuted).toHaveBeenCalledOnce();
});

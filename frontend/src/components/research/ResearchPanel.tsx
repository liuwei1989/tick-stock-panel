import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import type { ResearchSignal } from '@/lib/researchTypes'
import { loadHistory, openHistoryReport } from '@/lib/stockAnalysisStore'
import { ResearchArtifactView } from './ResearchArtifactView'
import { ResearchSchedulePanel } from './ResearchSchedulePanel'
import { PortfolioRiskPanel } from './PortfolioRiskPanel'
import { ResearchSkillPicker } from './ResearchSkillPicker'

const states = { watching: '观察中', review_required: '待复评', dismissed: '已归档', evaluated: '已有后验' }
const runStates: Record<string, string> = { running: '运行中', succeeded: '完成', degraded: '部分完成', failed: '失败', cancelled: '已取消', interrupted: '重启中断' }
export function ResearchPanel() {
  const qc = useQueryClient()
  const signals = useQuery({ queryKey: QK.researchSignals, queryFn: api.researchSignals, refetchInterval: 30_000 })
  const runs = useQuery({ queryKey: QK.researchRuns, queryFn: api.researchRuns, refetchInterval: 30_000 })
  const batches = useQuery({ queryKey: QK.researchBatches, queryFn: api.researchBatches, refetchInterval: 10_000 })
  const [symbols, setSymbols] = useState('')
  const [skillIds, setSkillIds] = useState<string[]>(['auto'])
  const [expanded, setExpanded] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [message, setMessage] = useState('')
  async function act(signal: ResearchSignal, action: 'outcome' | 'review_required' | 'dismissed') {
    setBusy(signal.id); setMessage('')
    try {
      if (action === 'outcome') {
        const result = await api.researchOutcome(signal.id, signal.version)
        if (result.outcome.status === 'pending') setMessage(`后续日线不足：已观察 ${result.outcome.observations} / 5 根。`)
      } else {
        await api.researchTransition(signal.id, action, signal.version, action === 'dismissed' ? '用户归档' : '用户请求重新核验')
      }
      await qc.invalidateQueries({ queryKey: QK.researchSignals })
    } catch (e) { setMessage(e instanceof Error ? e.message : '操作失败，请重试') }
    finally { setBusy(null) }
  }
  return <section className="rounded-card border border-border bg-surface/40 p-4" aria-label="研究与决策信号">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h2 className="text-sm font-semibold">研究与决策信号</h2>
      <button className="text-xs text-accent" onClick={() => { signals.refetch(); runs.refetch(); batches.refetch() }}>刷新</button>
    </div>
    <p className="mt-1 text-xs text-muted">查看 Agent 运行记录、研究证据与后验结果。观察收益按日线收盘计算。</p>
    <div className="mt-3"><ResearchSkillPicker label="批量研究 Skill" value={skillIds} onChange={setSkillIds} disabled={busy !== null} /></div>
    <form className="mt-3 flex flex-wrap gap-2" onSubmit={async e => {
      e.preventDefault(); setBusy('batch'); setMessage('')
      try {
        const codes = Array.from(new Set(symbols.trim().split(/[\s,，;；]+/).filter(Boolean)))
        if (!codes.length || codes.length > 20) throw new Error('请输入 1–20 个代码')
        await api.researchBatchStart(codes, crypto.randomUUID(), skillIds)
        await qc.invalidateQueries({ queryKey: QK.researchBatches })
        setMessage('批量研究已启动，可离开页面后返回查看进度。')
      } catch (err) { setMessage(err instanceof Error ? err.message : '启动失败') }
      finally { setBusy(null) }
    }}>
      <input aria-label="批量研究代码" value={symbols} onChange={e => setSymbols(e.target.value)} placeholder="000001.SZ, 600519.SH（最多 20 个）"
        className="min-w-0 flex-1 rounded border border-border bg-surface px-2 py-1 text-xs" />
      <button type="submit" disabled={busy !== null || batches.data?.batches.some(b => ['running','queued'].includes(b.status))}
        className="rounded border border-border px-3 py-1 text-xs text-accent disabled:opacity-50">批量 Agent 研究</button>
    </form>
    {batches.data?.batches.slice(0,3).map(b => <p key={b.id} className="mt-2 text-xs text-muted">
      批量研究 · {runStates[b.status] ?? (b.status === 'queued' ? '排队中' : b.status)} · 已处理 {b.items.length}/{b.symbols.length}
    </p>)}
    {batches.isError && <p role="alert" className="mt-2 text-xs text-danger">批量任务读取失败，请刷新重试。</p>}
    {message && <p role="status" className="mt-2 text-xs text-amber-500">{message}</p>}
    {signals.isLoading ? <p className="py-6 text-xs text-muted">正在读取研究记录…</p>
      : signals.isError ? <p role="alert" className="py-4 text-xs text-danger">研究记录加载失败，请刷新重试。</p>
      : !signals.data?.signals.length ? <p className="py-6 text-xs text-muted">尚无结构化研究。到个股分析选择研究框架并运行 Agent，结果会归档到这里。</p>
      : <div className="mt-3 max-h-[36rem] space-y-2 overflow-y-auto">
        {signals.data.signals.map(signal => <div key={signal.id} className="rounded border border-border/60 p-3">
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
            <button className="font-medium text-accent" onClick={() => setExpanded(expanded === signal.id ? null : signal.id)}>{signal.symbol} · {states[signal.status]}</button>
            <span className="text-muted">{signal.signal_date} · 第 {signal.version} 版</span>
          </div>
          <p className="mt-1 line-clamp-2 text-xs text-secondary">{signal.artifact.thesis.summary || '正文已归档，结构化结论待核验'}</p>
          {signal.outcome?.return_ratio != null && <p className="mt-1 text-xs">{signal.outcome.observations} 根日线观察收益：{(signal.outcome.return_ratio * 100).toFixed(2)}%</p>}
          {expanded === signal.id && <>
            <ResearchArtifactView artifact={signal.artifact} />
            <div className="flex flex-wrap gap-3 text-xs">
              <button className="text-accent" onClick={async () => { await loadHistory(); openHistoryReport(signal.report_id) }}>查看报告</button>
              {signal.status !== 'dismissed' && <>
                {signal.status !== 'evaluated' && <button disabled={busy !== null} onClick={() => act(signal, 'outcome')}>评估 5 根日线后验</button>}
                {signal.status !== 'review_required' && <button disabled={busy !== null} onClick={() => act(signal, 'review_required')}>标记待复评</button>}
                <button disabled={busy !== null} onClick={() => act(signal, 'dismissed')}>归档</button>
              </>}
            </div>
            {signal.history.length > 0 && <ol className="mt-3 space-y-1 text-[11px] text-muted">{signal.history.map((h,i) => <li key={i}>{h.at} · {h.reason}</li>)}</ol>}
          </>}
        </div>)}
      </div>}
    <ResearchSchedulePanel />
    <PortfolioRiskPanel />
    <details className="mt-4 border-t border-border pt-3 text-xs">
      <summary className="cursor-pointer text-secondary">最近 Agent 运行记录</summary>
      {runs.isError ? <p role="alert">运行记录加载失败</p> : runs.isLoading ? <p>加载中…</p> : !runs.data?.runs.length ? <p className="mt-2 text-muted">尚无运行记录</p> : runs.data.runs.slice(0,20).map(run => <div key={run.id} className="mt-2 border-b border-border/40 pb-2">
        <div>{run.symbol} · {runStates[run.status] ?? run.status}</div>
        <div className="mt-1 break-all text-muted">{run.trajectory.filter(t => t.status !== 'started').map(t => `${t.label} ${(t.duration_ms ?? 0)/1000}s${t.status === 'degraded' ? '（降级）' : ''}`).join(' → ') || run.created_at}</div>
      </div>)}
    </details>
  </section>
}

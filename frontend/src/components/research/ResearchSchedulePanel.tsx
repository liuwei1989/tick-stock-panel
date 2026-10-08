import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import type { ResearchSchedule } from '@/lib/researchTypes'
import { ResearchSkillPicker } from './ResearchSkillPicker'

const states: Record<string, string> = {
  waiting: '等待运行时间', holiday: '休市，已跳过', calendar_unknown: '交易日未确认，等待重试',
  started: '已启动', already_run: '今日已触发', busy_or_changed: '已有任务或今日配置已变更',
  data_pending: '标的当日日线不完整，等待同步', data_unavailable: '本地行情读取失败，等待重试',
}

export function ResearchSchedulePanel() {
  const qc = useQueryClient()
  const query = useQuery({ queryKey: QK.researchSchedule, queryFn: api.researchSchedule, refetchInterval: 60_000 })
  const [draft, setDraft] = useState<ResearchSchedule | null>(null)
  const [symbols, setSymbols] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const config = draft ?? query.data?.config
  return <details className="mt-3 border-t border-border pt-3 text-xs">
    <summary className="cursor-pointer text-secondary">定时研究 · {query.data?.config.enabled ? '已启用' : '未启用'}</summary>
    <p className="my-2 text-muted">按北京时间，收盘后每天最多运行一批。仅在确认是交易日时触发；休市跳过，日历未知时等待。使用当前模型配置，会消耗模型额度。中断任务需手动重试。</p>
    {query.isLoading ? <p>正在读取配置…</p> : query.isError ? <p role="alert">读取失败 <button onClick={() => query.refetch()}>重试</button></p> : config && <form className="space-y-3" onSubmit={async e => {
      e.preventDefault(); setBusy(true); setMessage('')
      try {
        const codes = (symbols ?? config.symbols.join(',')).trim().split(/[\s,，;；]+/).filter(Boolean)
        await api.researchScheduleSave({ ...config, symbols: codes })
        await qc.invalidateQueries({ queryKey: QK.researchSchedule })
        setDraft(null); setSymbols(null); setMessage('定时研究配置已保存')
      } catch (err) { setMessage(err instanceof Error ? err.message : '保存失败') }
      finally { setBusy(false) }
    }}>
      {!query.data?.scheduler_available && <p role="alert" className="text-warning">调度器当前不可用，请检查后端启动状态。</p>}
      <label className="flex items-center gap-2"><input type="checkbox" checked={config.enabled} onChange={e => setDraft({ ...config, enabled: e.target.checked })} />启用定时批量研究</label>
      <label className="block">研究代码（最多 20 个）<input aria-label="定时研究代码" value={symbols ?? config.symbols.join(', ')} onChange={e => setSymbols(e.target.value)}
        className="mt-1 w-full rounded border border-border bg-surface px-2 py-1" placeholder="000001.SZ, 600519.SH" /></label>
      <ResearchSkillPicker label="定时研究 Skill" value={config.skill_ids} onChange={skill_ids => setDraft({ ...config, skill_ids })} disabled={busy} />
      <div className="flex flex-wrap items-center gap-3">
        <label>触发时间 <input type="time" aria-label="定时研究时间" min="15:35" value={`${String(config.hour).padStart(2,'0')}:${String(config.minute).padStart(2,'0')}`}
          onChange={e => { const [hour, minute] = e.target.value.split(':').map(Number); if (Number.isFinite(hour) && Number.isFinite(minute)) setDraft({ ...config, hour, minute }) }} className="rounded border border-border bg-surface px-2 py-1" /></label>
        <label>分析模式 <select value={config.mode} onChange={e => setDraft({ ...config, mode: e.target.value as ResearchSchedule['mode'] })} className="rounded border border-border bg-surface px-2 py-1">
          <option value="quick">快速</option><option value="standard">标准</option><option value="full">完整 Agent</option>
        </select></label>
        <button type="submit" disabled={busy} className="text-accent disabled:opacity-50">保存配置</button>
      </div>
      {query.data?.last_check && <p className="text-muted">最近检查：{query.data.last_check.at} · {states[query.data.last_check.status] ?? '等待下一次检查'}</p>}
    </form>}
    {message && <p role="status" className="mt-2 text-warning">{message}</p>}
  </details>
}

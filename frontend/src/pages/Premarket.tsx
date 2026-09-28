/**
 * 盘前结构化研报页 — 概念 → 核心逻辑 → 催化事件 → 核心股票。
 *
 * 数据:
 *  - 上下文:   GET /api/premarket-report/context   (规则版素材预览)
 *  - 生成:     POST /api/premarket-report/generate (流式 AI; AI 未配置降级规则版)
 *  - 历史:     GET /api/premarket-report/list
 *
 * 布局: 顶部工具栏(日期 + 关注点 + 生成按钮) → 规则版素材卡 → 研报正文 → 历史列表。
 */
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, RefreshCw, Sparkles, FileText, History, AlertTriangle } from 'lucide-react'
import { api, type PremarketReport } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { cn } from '@/lib/cn'
import { PageHeader } from '@/components/PageHeader'
import { toast } from '@/components/Toast'

function LevelBadge({ level }: { level: string }) {
  const map: Record<string, [string, string]> = {
    gold: ['bg-yellow-500/15 text-yellow-400 border-yellow-500/40', '金牌'],
    up: ['bg-emerald-500/15 text-emerald-400 border-emerald-500/40', '主升'],
    pulse: ['bg-orange-500/15 text-orange-400 border-orange-500/40', '脉冲'],
    rotate: ['bg-slate-500/15 text-slate-400 border-slate-500/40', '轮动'],
  }
  const [cls, label] = map[level] ?? map.rotate
  return (
    <span className={cn('inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium', cls)}>
      {label}
    </span>
  )
}

export default function Premarket() {
  const qc = useQueryClient()
  const [date, setDate] = useState('')
  const [focus, setFocus] = useState('')
  const [content, setContent] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [lastReport, setLastReport] = useState<PremarketReport | null>(null)

  const contextQ = useQuery({ queryKey: QK.premarketReport, queryFn: () => api.premarketContext() })
  const listQ = useQuery({ queryKey: QK.premarketList, queryFn: () => api.premarketList(30) })
  const ctx = contextQ.data

  const generate = async () => {
    setStreaming(true)
    setContent('')
    setLastReport(null)
    try {
      for await (const ev of api.premarketGenerate(date || undefined, focus)) {
        if (ev.type === 'meta' && ev.fallback) {
          toast('AI 未配置, 已生成规则版研报')
        }
        if (ev.type === 'delta') setContent(prev => prev + (ev.content ?? ''))
        if (ev.type === 'done') {
          setLastReport(ev.report ?? null)
          qc.invalidateQueries({ queryKey: QK.premarketList })
        }
        if (ev.type === 'error') toast(ev.message ?? '生成失败', 'error')
      }
    } catch (e) {
      toast(e instanceof Error ? e.message : '生成失败', 'error')
    } finally {
      setStreaming(false)
    }
  }

  const report = lastReport ?? (content ? { ...ctx!, ai: true, content } : null)

  return (
    <div className="space-y-4 pb-10">
      <PageHeader
        title="盘前研报"
        subtitle="概念 → 核心逻辑 → 催化事件 → 核心股票"
        right={
          <button className="btn btn-ghost btn-sm" onClick={() => { contextQ.refetch(); listQ.refetch() }}>
            <RefreshCw className={cn('size-4', contextQ.isFetching && 'animate-spin')} /> 刷新
          </button>
        }
      />

      {/* 工具栏 */}
      <div className="mx-5 flex flex-wrap items-end gap-3 rounded-xl border bg-base-200/60 p-3">
        <div className="flex flex-col gap-1">
          <label className="text-[11px] text-muted">日期 (留空=今日)</label>
          <input type="date" value={date} onChange={e => setDate(e.target.value)}
            className="input input-sm input-bordered" />
        </div>
        <div className="flex min-w-[200px] flex-1 flex-col gap-1">
          <label className="text-[11px] text-muted">关注点 (可选)</label>
          <input value={focus} onChange={e => setFocus(e.target.value)} placeholder="如: AI 算力 / 商业航天"
            className="input input-sm input-bordered" />
        </div>
        <button className="btn btn-primary btn-sm" onClick={generate} disabled={streaming}>
          {streaming ? <Loader2 className="size-4 animate-spin" /> : <Sparkles className="size-4" />}
          {streaming ? '生成中…' : '生成研报'}
        </button>
      </div>

      {ctx && !ctx.available && (
        <div className="mx-5 flex items-center gap-2 rounded-xl border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-400">
          <AlertTriangle className="size-4" /> {ctx.summary}
        </div>
      )}

      <div className="mx-5 grid gap-4 lg:grid-cols-5">
        {/* 规则版素材 */}
        {ctx && (
          <div className="rounded-xl border bg-base-200/60 p-4 lg:col-span-2">
            <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
              <FileText className="size-4" /> 结构化素材
            </div>
            {ctx.available ? (
              <div className="space-y-3 text-sm">
                <div><span className="text-muted">环境: </span>{ctx.environment}</div>
                <div className="space-y-1.5">
                  {ctx.mainline_top.map(i => (
                    <div key={i.member} className="flex items-center justify-between gap-2">
                      <div className="flex min-w-0 items-center gap-1.5">
                        <LevelBadge level={i.level} />
                        <span className="truncate">{i.member}</span>
                      </div>
                      <div className="shrink-0 text-xs text-muted">
                        {i.limit_up_count} 涨停 · {i.max_boards} 板 · 身位 {i.leader_symbol}
                      </div>
                    </div>
                  ))}
                </div>
                {ctx.catalysts.length > 0 && (
                  <div className="border-t border-base-300 pt-2">
                    <div className="mb-1 text-xs font-medium text-muted">催化锚点 (待验证)</div>
                    {ctx.catalysts.map(c => (
                      <div key={c.member} className="text-xs text-muted">· {c.event}</div>
                    ))}
                  </div>
                )}
                {ctx.plan && (
                  <div className="border-t border-base-300 pt-2 text-xs text-muted">
                    今日计划 {ctx.plan.plan_id} · {ctx.plan.entries} 标的 · {ctx.plan.status}
                  </div>
                )}
              </div>
            ) : (
              <p className="text-sm text-muted">{ctx.summary}</p>
            )}
          </div>
        )}

        {/* 研报正文 */}
        <div className="rounded-xl border bg-base-200/60 p-4 lg:col-span-3">
          <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
            <Sparkles className="size-4" /> 研报正文
            {report?.ai && <span className="rounded-full bg-sky-500/10 px-2 py-0.5 text-[11px] text-sky-400">AI</span>}
            {report && !report.ai && <span className="rounded-full bg-base-300 px-2 py-0.5 text-[11px] text-muted">规则版</span>}
          </div>
          {streaming && (
            <div className="mb-2 flex items-center gap-2 text-xs text-muted">
              <Loader2 className="size-3 animate-spin" /> AI 生成中…
            </div>
          )}
          {report?.content ? (
            <pre className="whitespace-pre-wrap font-sans text-sm leading-relaxed">{report.content}</pre>
          ) : report?.summary ? (
            <pre className="whitespace-pre-wrap font-sans text-sm leading-relaxed text-muted">{report.summary}</pre>
          ) : (
            <p className="text-sm text-muted">
              {ctx?.available ? '点击"生成研报"输出研报正文; AI 未配置时自动生成规则版。' : '主线时序未生成, 研报暂不可用 (需先同步日线并构建 enriched)。'}
            </p>
          )}
        </div>
      </div>

      {/* 历史研报 */}
      {listQ.data && listQ.data.reports.length > 0 && (
        <div className="mx-5 rounded-xl border bg-base-200/60 p-4">
          <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
            <History className="size-4" /> 历史研报
          </div>
          <div className="space-y-2">
            {listQ.data.reports.slice(0, 10).map(r => (
              <button key={r.as_of} className="flex w-full items-center justify-between gap-2 rounded-lg border border-base-300 bg-base/40 px-3 py-2 text-left transition-colors hover:border-accent/40"
                onClick={() => { setDate(r.as_of); setContent(r.content ?? ''); setLastReport(r) }}>
                <div className="flex min-w-0 items-center gap-2 text-sm">
                  <FileText className="size-3.5 shrink-0 text-muted" />
                  <span className="font-mono text-xs">{r.as_of}</span>
                  <span className={cn('text-xs', r.ai ? 'text-sky-400' : 'text-muted')}>{r.ai ? 'AI' : '规则'}</span>
                </div>
                <span className="truncate pl-4 text-xs text-muted">{r.summary.split('\n')[0]}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

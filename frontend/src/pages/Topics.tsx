/**
 * 题材表格化分析页 — 题材列表 → 成分股 → OCR 导入 → 回测入口。
 *
 * 数据:
 *  - 主表:    GET /api/topic-table
 *  - 成分股:  GET /api/topic-table/{topic}/members   (ext + 自定义合并, 身位股置顶)
 *  - OCR:     POST /api/topic-table/{topic}/ocr-import → 候选 → PUT 保存
 *  - 回测:    成分股复用现有回测页 (/backtest)
 */
import { Fragment, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { RefreshCw, Table2, FileImage, Loader2, Search, Check, X } from 'lucide-react'
import { api, type TopicOcrResult } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { cn } from '@/lib/cn'
import { PageHeader } from '@/components/PageHeader'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { toast } from '@/components/Toast'

function LevelBadge({ level }: { level: string }) {
  const map: Record<string, [string, string]> = {
    gold: ['bg-yellow-500/15 text-yellow-400 border-yellow-500/40', '金牌'],
    up: ['bg-emerald-500/15 text-emerald-400 border-emerald-500/40', '主升'],
    pulse: ['bg-orange-500/15 text-orange-400 border-orange-500/40', '脉冲'],
    rotate: ['bg-slate-500/15 text-slate-400 border-slate-500/40', '轮动'],
  }
  const [cls, label] = map[level] ?? map.rotate
  return <span className={cn('inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium', cls)}>{label}</span>
}

export default function Topics() {
  const qc = useQueryClient()
  const [search, setSearch] = useState('')
  const [expanded, setExpanded] = useState<string | null>(null)
  const [ocrTopic, setOcrTopic] = useState<string | null>(null)
  const [ocrCandidates, setOcrCandidates] = useState<string[]>([])
  const [ocrResult, setOcrResult] = useState<TopicOcrResult | null>(null)

  const tableQ = useQuery({ queryKey: QK.topicTable, queryFn: api.topicTable })
  const membersQ = useQuery({
    queryKey: QK.topicMembers(expanded ?? ''),
    queryFn: () => api.topicMembers(expanded!),
    enabled: !!expanded,
  })

  const rows = (tableQ.data?.rows ?? []).filter(r =>
    !search || r.member.toLowerCase().includes(search.toLowerCase()))

  const ocrMut = useMutation({
    mutationFn: ({ topic, file }: { topic: string; file: File }) => api.topicOcrImport(topic, file),
    onSuccess: (r) => {
      if (!r.ok && r.message) { toast(r.message, 'error'); return }
      setOcrResult(r)
      setOcrCandidates((r.candidates ?? []).filter(c => c.matched).map(c => c.symbol))
    },
  })
  const saveMut = useMutation({
    mutationFn: ({ topic, symbols }: { topic: string; symbols: string[] }) => api.topicSaveMembers(topic, symbols),
    onSuccess: (_, v) => {
      toast(`已保存 ${v.symbols.length} 只成分股`, 'success')
      setOcrResult(null)
      setOcrTopic(null)
      qc.invalidateQueries({ queryKey: QK.topicTable })
      if (expanded) qc.invalidateQueries({ queryKey: QK.topicMembers(expanded) })
    },
  })

  return (
    <div className="space-y-4 pb-10">
      <PageHeader
        title="题材表格"
        subtitle="题材强度 / 成分股 / OCR 导入 / 回测入口"
        right={
          <Button variant="ghost" size="sm" onClick={() => tableQ.refetch()}>
            <RefreshCw className={cn('size-4', tableQ.isFetching && 'animate-spin')} /> 刷新
          </Button>
        }
      />

      {tableQ.data && !tableQ.data.available && (
        <div className="mx-5 flex items-center gap-2 rounded-xl border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-400">
          <Table2 className="size-4" /> {tableQ.data.detail ?? '题材数据未生成'}
        </div>
      )}

      {tableQ.data?.available && (
        <div className="mx-5 space-y-3">
          <div className="flex items-center gap-2">
            <div className="relative flex-1">
              <Search className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted" />
              <Input value={search} onChange={e => setSearch(e.target.value)} placeholder="搜索题材…"
                className="w-full pl-8" size="sm" />
            </div>
            <span className="text-xs text-muted">
              金牌 {tableQ.data.gold_count} · 共 {tableQ.data.rows.length} 题材 · 截至 {tableQ.data.as_of}
            </span>
          </div>

          <div className="overflow-hidden rounded-card border border-border bg-surface">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border bg-base/40 text-left text-[11px] uppercase tracking-wider text-muted">
                  <th className="px-3 py-2">题材</th>
                  <th className="px-3 py-2">认证</th>
                  <th className="px-3 py-2 text-right">强度</th>
                  <th className="px-3 py-2 text-right">5日均</th>
                  <th className="px-3 py-2 text-right">居前</th>
                  <th className="px-3 py-2 text-right">涨停</th>
                  <th className="px-3 py-2 text-right">最高板</th>
                  <th className="px-3 py-2">身位股</th>
                  <th className="px-3 py-2 text-right">成分股</th>
                  <th className="px-3 py-2 text-right">操作</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(r => (
                  <Fragment key={r.member}>
                    <tr className={cn('border-b border-border/60 hover:bg-base/40', expanded === r.member && 'bg-base/40')}>
                      <td className="px-3 py-2 font-medium">{r.member}</td>
                      <td className="px-3 py-2"><LevelBadge level={r.level} /></td>
                      <td className="px-3 py-2 text-right font-mono">{r.score.toFixed(0)}</td>
                      <td className="px-3 py-2 text-right font-mono text-muted">{r.avg5_score.toFixed(1)}</td>
                      <td className="px-3 py-2 text-right font-mono">{r.streak_days}日</td>
                      <td className="px-3 py-2 text-right font-mono">{r.limit_up_count}</td>
                      <td className="px-3 py-2 text-right font-mono">{r.max_boards}</td>
                      <td className="px-3 py-2 font-mono">{r.leader_symbol ?? '—'}</td>
                      <td className="px-3 py-2 text-right font-mono">{r.member_count}</td>
                      <td className="px-3 py-2 text-right">
                        <Button variant="ghost" size="xs" onClick={() => setExpanded(expanded === r.member ? null : r.member)}>
                          {expanded === r.member ? '收起' : '成分股'}
                        </Button>
                      </td>
                    </tr>
                    {expanded === r.member && (
                      <tr key={`${r.member}-detail`} className="bg-base/30">
                        <td colSpan={10} className="px-3 py-3">
                          <div className="space-y-2">
                            {/* 成分股 */}
                            <div className="flex flex-wrap gap-1.5">
                              {membersQ.isLoading && <Loader2 className="size-4 animate-spin text-muted" />}
                              {(membersQ.data?.members ?? []).map(m => (
                                <span key={m.symbol} className={cn('inline-flex items-center gap-1 rounded-lg border px-2 py-1 text-xs',
                                  m.source === '身位股' ? 'border-yellow-500/40 bg-yellow-500/10 text-yellow-400'
                                    : m.source === '自定义' ? 'border-sky-500/40 bg-sky-500/10 text-sky-400'
                                    : 'border-border bg-elevated/50 text-foreground')}>
                                  {m.symbol}
                                  {m.source !== 'ext' && <span className="text-[9px] opacity-70">{m.source === '身位股' ? '身位' : '自'}</span>}
                                </span>
                              ))}
                              {membersQ.data && membersQ.data.members.length === 0 && (
                                <span className="text-xs text-muted">暂无成分股 — 可用 OCR 导入题材成分截图</span>
                              )}
                            </div>
                            {/* OCR 导入 */}
                            <div className="flex flex-wrap items-center gap-2">
                              <Button variant="ghost" size="xs" onClick={() => { setOcrTopic(r.member); setOcrResult(null); setOcrCandidates([]) }}>
                                <FileImage className="size-3.5" /> OCR 导入成分
                              </Button>
                              {ocrTopic === r.member && (
                                <label className="inline-flex h-6 cursor-pointer select-none items-center justify-center gap-1.5 rounded-btn bg-accent px-2 text-[11px] font-medium text-white transition-colors hover:bg-accent/90 disabled:opacity-50 disabled:pointer-events-none">
                                  <FileImage className="size-3.5" /> 上传截图
                                  <input type="file" accept="image/*" className="hidden"
                                    onChange={e => {
                                      const f = e.target.files?.[0]
                                      if (f) ocrMut.mutate({ topic: r.member, file: f })
                                    }} />
                                </label>
                              )}
                              {ocrMut.isPending && ocrTopic === r.member && <Loader2 className="size-3 animate-spin text-muted" />}
                              {ocrResult && ocrTopic === r.member && (
                                <div className="flex w-full flex-wrap items-center gap-2 rounded-lg border border-border bg-base/40 p-2">
                                  <span className="text-xs text-muted">识别 {ocrResult.matched_count ?? 0} 只:</span>
                                  {(ocrResult.candidates ?? []).filter(c => c.matched).map(c => (
                                    <label key={c.symbol} className="flex cursor-pointer items-center gap-1 rounded border border-border bg-elevated/50 px-1.5 py-0.5 text-xs">
                                      <input type="checkbox" className="h-3 w-3 accent-accent" checked={ocrCandidates.includes(c.symbol)}
                                        onChange={e => setOcrCandidates(prev => e.target.checked ? [...prev, c.symbol] : prev.filter(x => x !== c.symbol))} />
                                      {c.symbol} {c.name}
                                    </label>
                                  ))}
                                  <div className="ml-auto flex gap-1">
                                    <Button variant="ghost" size="xs" onClick={() => { setOcrResult(null); setOcrTopic(null) }}><X className="size-3" /> 取消</Button>
                                    <Button variant="primary" size="xs" disabled={saveMut.isPending}
                                      onClick={() => saveMut.mutate({ topic: r.member, symbols: [...new Set([...(membersQ.data?.members ?? []).map(m => m.symbol), ...ocrCandidates])] })}>
                                      <Check className="size-3" /> 保存
                                    </Button>
                                  </div>
                                </div>
                              )}
                            </div>
                            {r.member_count > 0 && (
                              <a href="/backtest" className="text-xs text-accent hover:underline">
                                去回测页验证成分股表现 →
                              </a>
                            )}
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {tableQ.isLoading && <div className="grid min-h-[30vh] place-items-center"><Loader2 className="size-6 animate-spin text-muted" /></div>}
    </div>
  )
}

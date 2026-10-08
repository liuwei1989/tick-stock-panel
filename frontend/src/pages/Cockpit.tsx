import { ResearchPanel } from '@/components/research/ResearchPanel'
/**
 * 驾驶舱 (Cockpit) — 一屏聚合数据链路/市场环境/主线认证/工作流状态/异常提醒。
 *
 * 目标: "一眼就能发现各种情况"。
 *  - 顶部状态灯: 全绿(ok) / 有提醒(attention), 异常自动高亮
 *  - 交易节点: 盘前→竞价→早盘→午间→午盘→复盘 时间线 + 当日数据就绪提示
 *  - 市场脉搏: zzshare 实时模块速览 (涨停/炸板/连板梯队/情绪K线/题材热度/人气榜/AI研报/监管预警)
 *  - 数据链路: 日线 → 富化 → 环境 → 主线 → 计划 → 复盘 逐层分区/最新日期, 缺口标红
 *  - 主线认证: 金牌主升/主升/脉冲/轮动 + 龙头身位股
 *  - 环境: regime 分数 + state/phase
 *  - 工作流: 最新计划/复盘/进化状态
 *  - 提醒列表: error/warn/info 分级, 每条带一键处理动作
 *
 * 数据: GET /api/cockpit/overview (交易日自动刷新, 每 60s)
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Loader2, RefreshCw, CircleAlert, TriangleAlert, Info,
  Gauge, Flame, Activity, Database, TrendingUp, ShieldCheck,
  Clock, Check, X, Play, Radio, Layers, Star, BookOpen,
  Sparkles,
} from 'lucide-react'
import { api } from '@/lib/api'
import type { CockpitAlert, CockpitHealthLayer, CockpitSession, CockpitZzshare, DailyBrief } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { cn } from '@/lib/cn'
import { fmtPct, priceColorClass } from '@/lib/format'
import { PageHeader } from '@/components/PageHeader'
import { Button } from '@/components/ui/Button'

/** 驾驶舱可执行动作 (与后端 alert.action 对应)。 */
type ActionKind =
  | 'pipeline' | 'rebuild_enriched' | 'sync_minute' | 'regime_recompute'
  | 'mainline_recompute' | 'generate_plan' | 'review_plan' | 'evolution_run'

/** 立即型动作 → 返回提示 + 可选 job_id (长任务轮询进度)。 */
async function runAction(
  kind: ActionKind, payload?: Record<string, unknown>,
): Promise<{ message: string; jobId?: string }> {
  switch (kind) {
    case 'pipeline': {
      const r = await api.pipelineRun()
      return { message: r.reused ? '已有盘后管道任务在运行' : '盘后管道已启动', jobId: r.job_id }
    }
    case 'rebuild_enriched': {
      const r = await api.rebuildEnriched()
      return { message: '富化重建已启动', jobId: r.job_id }
    }
    case 'sync_minute': {
      const r = await api.syncMinute()
      return { message: '分钟同步已启动', jobId: r.job_id }
    }
    case 'regime_recompute': {
      const r = await api.regimeRecompute()
      return { message: `环境分已重算 ${r.computed} 天` }
    }
    case 'mainline_recompute': {
      const r = await api.regimeMainlineRecompute()
      return { message: `主线已重算 ${r.rows} 行` }
    }
    case 'generate_plan': {
      const r = await api.workflowGeneratePlan({})
      return { message: `计划 ${r.plan_id} 已生成 (${r.entries?.length ?? 0} 标的)` }
    }
    case 'review_plan': {
      const r = await api.workflowReviewPlan(String(payload?.plan_id ?? ''), {})
      const rate = r.summary?.win_rate
      return { message: `复盘完成${rate != null ? ` (胜率 ${rate.toFixed(0)}%)` : ''}` }
    }
    default:
      throw new Error(`未知动作: ${kind}`)
  }
}

/** 轮询长任务直到结束; 失败抛错。 */
async function waitJob(jobId: string, onProgress: (stage: string, pct: number) => void): Promise<void> {
  for (;;) {
    const j = await api.pipelineJob(jobId)
    onProgress(j.stage || j.status, j.progress ?? 0)
    if (j.status === 'succeeded') return
    if (j.status === 'failed') throw new Error('任务执行失败, 详见数据页任务日志')
    await new Promise((r) => setTimeout(r, 1500))
  }
}

const LEVEL_META = {
  error: { icon: CircleAlert, cls: 'bg-red-500/10 text-red-400 border-red-500/30' },
  warn: { icon: TriangleAlert, cls: 'bg-amber-500/10 text-amber-400 border-amber-500/30' },
  info: { icon: Info, cls: 'bg-sky-500/10 text-sky-400 border-sky-500/30' },
} as const

function HealthDot({ layer }: { layer: CockpitHealthLayer }) {
  const fr = layer.freshness?.status ?? (layer.partitions > 0 ? 'fresh' : 'missing')
  const lag = layer.freshness?.lag_days
  const dot = fr === 'fresh' ? 'bg-emerald-500'
    : fr === 'stale' ? 'bg-amber-500 animate-pulse'
    : 'bg-red-500 animate-pulse'
  const box = fr === 'fresh' ? 'bg-surface border-border'
    : fr === 'stale' ? 'bg-amber-500/5 border-amber-500/30'
    : 'bg-red-500/5 border-red-500/30'
  const sub = fr === 'fresh' ? `${layer.partitions} 分区`
    : fr === 'stale' ? `滞后 ${lag ?? '?'} 天`
    : '缺失'
  return (
    <div className={cn('flex flex-col gap-1 rounded-card border p-3', box)}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-medium">{layer.label}</span>
        <span className={cn('size-2.5 rounded-full', dot)} />
      </div>
      <div className={cn('text-xs', fr === 'stale' ? 'text-amber-400' : 'text-muted')}>{sub}</div>
      <div className="text-[11px] text-muted/80">{layer.latest ?? '—'}</div>
    </div>
  )
}

const SESSION_STATE_META: Record<CockpitSession['nodes'][number]['state'], { cls: string; dot: string; label: string }> = {
  done: { cls: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30', dot: 'bg-emerald-500', label: '已结束' },
  active: { cls: 'bg-accent/10 text-accent border-accent/40', dot: 'bg-accent animate-pulse', label: '进行中' },
  pending: { cls: 'bg-surface text-muted border-border', dot: 'bg-muted/40', label: '待开始' },
}

function SessionTimeline({ session }: { session: CockpitSession }) {
  return (
    <div className="rounded-card border border-border bg-surface p-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <Clock className="size-4" /> 交易节点
          <span className="rounded-full bg-elevated px-2 py-0.5 text-xs font-normal text-foreground">当前：{session.current}</span>
        </div>
        <span className="text-[11px] text-muted/70">
          {session.as_of_time} · {session.trading_day ? '交易日' : '休市'}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
        {session.nodes.map((n) => {
          const m = SESSION_STATE_META[n.state]
          return (
            <div key={n.key} className={cn('rounded-lg border px-2.5 py-2', m.cls)}>
              <div className="flex items-center justify-between gap-1">
                <span className="text-sm font-medium">{n.label}</span>
                <span className={cn('size-2 rounded-full', m.dot)} />
              </div>
              <div className="mt-1 flex items-center justify-between text-[10px] text-muted/80">
                <span>{n.window}</span>
                {n.data_ready === true && <Check className="size-3 text-emerald-400" />}
                {n.data_ready === false && <X className="size-3 text-red-400" />}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function DailyBriefCard({ brief, onGenerate }: { brief: DailyBrief | null; onGenerate: () => Promise<void> }) {
  const [busy, setBusy] = useState(false)
  const sentimentCls = brief?.sentiment === '偏强' ? 'text-emerald-400' : brief?.sentiment === '偏弱' ? 'text-red-400' : 'text-amber-400'
  return <section className="rounded-card border border-accent/30 bg-accent/5 p-4" aria-label="每日行动摘要">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div className="flex items-center gap-2 text-sm font-semibold"><Sparkles className="size-4 text-accent" /> 每日行动摘要
        {brief && <span className="rounded-full border border-border px-2 py-0.5 text-[10px] font-normal text-muted">{brief.source === 'agent' ? 'AI Agent' : '规则降级'}</span>}
      </div>
      <button className="text-xs text-accent disabled:opacity-50" disabled={busy} onClick={async () => { setBusy(true); try { await onGenerate() } finally { setBusy(false) } }}>
        {busy ? '生成中…' : '重新生成'}
      </button>
    </div>
    {!brief ? <p className="mt-3 text-sm text-muted">尚未生成。点击“重新生成”，Agent 会结合市场环境、研究信号和模拟盘持仓给出今日摘要。</p> : <>
      <div className="mt-3 rounded-lg border border-border/70 bg-surface/70 p-3">
        <div className="flex items-baseline gap-2"><span className="text-xs text-muted">市场情绪</span><span className={`text-xl font-bold ${sentimentCls}`}>{brief.sentiment}</span>{brief.sentiment_score != null && <span className="text-xs text-muted">{brief.sentiment_score.toFixed(0)}分</span>}</div>
        <div className="mt-1 text-xs text-secondary">{brief.sentiment_reason}</div>
      </div>
      <div className="mt-3 grid gap-3 md:grid-cols-2">
        <div className="rounded-lg border border-emerald-500/20 bg-emerald-500/5 p-3"><div className="text-xs font-medium text-emerald-400">怎么买 / 关注</div>
          {brief.buy.length ? brief.buy.map(item => <div key={item.symbol} className="mt-2 text-xs"><div className="font-medium">{item.symbol} · {item.action}</div><div className="text-secondary">{item.reason}</div><div className="text-muted">确认条件：{item.condition}</div></div>) : <div className="mt-2 text-xs text-muted">暂无有证据支持的标的</div>}
        </div>
        <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-3"><div className="text-xs font-medium text-amber-400">怎么卖 / 持有复核</div>
          {brief.sell.length ? brief.sell.map(item => <div key={item.symbol} className="mt-2 text-xs"><div className="font-medium">{item.symbol} · {item.action}</div><div className="text-secondary">{item.reason}</div><div className="text-muted">复核条件：{item.condition}</div></div>) : <div className="mt-2 text-xs text-muted">暂无持仓卖出提醒</div>}
        </div>
      </div>
      <div className="mt-3 text-[11px] text-muted">{brief.disclaimer} · {brief.generated_at}</div>
    </>}
  </section>
}

// ───────────────────────── 市场脉搏 (zzshare 实时速览) ─────────────────────────

function PulseChip({ label, value, sub, onClick }: {
  label: string
  value: React.ReactNode
  sub?: string
  onClick?: () => void
}) {
  return (
    <div
      className={cn(
        'flex flex-col gap-0.5 rounded-lg border border-border bg-surface p-3',
        onClick && 'cursor-pointer transition-colors hover:border-accent/50',
      )}
      onClick={onClick}
    >
      <span className="text-xs text-muted">{label}</span>
      <span className="text-lg font-semibold leading-7">{value}</span>
      {sub && <span className="text-[11px] text-muted/80">{sub}</span>}
    </div>
  )
}

function PulseCardHeader({ icon: Icon, title, date, onOpen }: {
  icon: typeof Radio
  title: string
  date?: string | null
  onOpen?: () => void
}) {
  return (
    <div className="mb-3 flex items-center justify-between gap-2">
      <div className="flex items-center gap-2 text-sm font-semibold">
        <Icon className="size-4" /> {title}
        {date && <span className="text-[11px] font-normal text-muted/70">截至 {date.slice(5)}</span>}
      </div>
      {onOpen && (
        <button
          className="text-[11px] text-muted transition-colors hover:text-accent"
          onClick={onOpen}
        >
          详情 →
        </button>
      )}
    </div>
  )
}

function MarketPulse({ zz }: { zz: CockpitZzshare }) {
  const navigate = useNavigate()
  const up = zz.uplimit
  const topics = zz.topics
  const hot = zz.hot
  const sent = zz.sentiment
  const lhb = zz.lhb
  const ai = zz.ai_reports
  const mv = zz.movement
  if (![up, topics, hot, sent, lhb, ai, mv].some(m => m?.available)) return null

  // 情绪K线日内变化 (p_close 相对 p_open)
  const sentDelta = sent.p_close != null && sent.p_open
    ? ((sent.p_close - sent.p_open) / sent.p_open) * 100 : null
  // 涨停梯队: 过滤空档位, 按板数降序
  const ladder = Object.entries(up.ladder ?? {})
    .map(([k, v]) => ({ boards: Number(k), count: v }))
    .filter(e => Number.isFinite(e.boards) && e.count > 0)
    .sort((a, b) => b.boards - a.boards)
  const ladderMax = Math.max(...ladder.map(e => e.count), 1)

  return (
    <div>
      <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-muted">
        <Radio className="size-4" /> 市场脉搏
      </div>
      <div className="space-y-4">
        {/* 速览指标条 */}
        <div className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-6">
          {up.available && (
            <PulseChip
              label="涨停" value={<span>{up.count ?? 0}<span className="ml-0.5 text-xs font-normal text-muted">家</span></span>}
              sub={`炸板 ${up.broken_count ?? 0}`}
              onClick={() => navigate('/limit-ladder')}
            />
          )}
          {up.available && (
            <PulseChip label="最高连板" value={<span>{up.max_consecutive ?? 0}<span className="ml-0.5 text-xs font-normal text-muted">板</span></span>} />
          )}
          {sent.available && (
            <PulseChip
              label="情绪K线"
              value={sentDelta == null ? '—' : (
                <span className={priceColorClass(sentDelta)}>
                  {sentDelta > 0 ? '+' : ''}{sentDelta.toFixed(2)}%
                </span>
              )}
            />
          )}
          {lhb.available && (
            <PulseChip
              label="龙虎榜" value={<span>{lhb.count ?? 0}<span className="ml-0.5 text-xs font-normal text-muted">家</span></span>}
              onClick={() => navigate('/dragon-tiger')}
            />
          )}
          {mv.available && (
            <PulseChip label="监管预警" value={<span>{mv.count ?? 0}<span className="ml-0.5 text-xs font-normal text-muted">家</span></span>} />
          )}
          {ai.available && (
            <PulseChip label="AI研报" value={<span>{ai.count ?? 0}<span className="ml-0.5 text-xs font-normal text-muted">篇</span></span>} />
          )}
        </div>

        <div className="grid gap-4 lg:grid-cols-2">
          {/* 题材热度 */}
          {topics.available && (topics.top?.length ?? 0) > 0 && (
            <div className="rounded-card border border-border bg-surface p-4">
              <PulseCardHeader icon={Layers} title="题材热度" date={topics.date} onOpen={() => navigate('/topics')} />
              <div className="space-y-1.5">
                {(topics.top ?? []).slice(0, 6).map(t => (
                  <div key={t.plate_name} className="flex items-center justify-between gap-2 text-sm">
                    <div className="flex min-w-0 items-center gap-1.5">
                      {t.is_new && <span className="shrink-0 rounded bg-sky-500/15 px-1 text-[10px] text-sky-400">新</span>}
                      <span className="truncate">{t.plate_name}</span>
                      {t.leader && <span className="truncate text-xs text-muted/80">· {t.leader}</span>}
                    </div>
                    <div className="flex shrink-0 items-center gap-2 text-xs text-muted">
                      <span className={priceColorClass(t.rate)}>{fmtPct((t.rate ?? 0) / 100, 2)}</span>
                      {t.days != null && t.days > 0 && <span className="text-muted/60">连{t.days}日</span>}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 人气榜 */}
          {hot.available && (hot.top?.length ?? 0) > 0 && (
            <div className="rounded-card border border-border bg-surface p-4">
              <PulseCardHeader icon={Star} title="人气榜" date={hot.date} />
              <div className="space-y-1.5">
                {(hot.top ?? []).slice(0, 6).map(h => (
                  <div key={`${h.rank}-${h.code}`} className="flex items-center justify-between gap-2 text-sm">
                    <div className="flex min-w-0 items-center gap-1.5">
                      <span className="w-6 shrink-0 text-right font-mono text-xs text-muted">#{h.rank}</span>
                      <span className="truncate">{h.name}</span>
                    </div>
                    <div className="flex shrink-0 items-center gap-2 text-xs">
                      <span className={priceColorClass(h.last_pct)}>{fmtPct((h.last_pct ?? 0) / 100, 2)}</span>
                      {h.rank_diff != null && h.rank_diff !== 0 && (
                        <span className={h.rank_diff > 0 ? 'text-sky-400' : 'text-orange-400'}>
                          {h.rank_diff > 0 ? `↑${h.rank_diff}` : `↓${-h.rank_diff}`}
                        </span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 涨停梯队 */}
          {up.available && ladder.length > 0 && (
            <div className="rounded-card border border-border bg-surface p-4">
              <PulseCardHeader icon={Flame} title="涨停梯队" date={up.date} onOpen={() => navigate('/limit-ladder')} />
              <div className="space-y-1.5">
                {ladder.map(e => (
                  <div key={e.boards} className="flex items-center gap-2 text-xs">
                    <span className="w-7 shrink-0 text-muted">{e.boards}板</span>
                    <div className="h-2 flex-1 overflow-hidden rounded-full bg-elevated">
                      <div className="h-full rounded-full bg-bull" style={{ width: `${(e.count / ladderMax) * 100}%` }} />
                    </div>
                    <span className="w-6 shrink-0 text-right text-muted">{e.count}</span>
                  </div>
                ))}
                {(up.sample ?? []).slice(0, 3).map(s => (
                  <div key={s.name} className="flex items-center justify-between gap-2 pt-1 text-sm">
                    <span className="truncate">{s.name}</span>
                    <span className="shrink-0 text-xs text-muted">
                      {s.board_desc}{s.first_time ? ` · ${s.first_time}` : ''}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* AI研报 */}
          {ai.available && (ai.titles?.length ?? 0) > 0 && (
            <div className="rounded-card border border-border bg-surface p-4">
              <PulseCardHeader icon={BookOpen} title="AI研报" />
              <div className="space-y-2">
                {(ai.titles ?? []).slice(0, 3).map((t, i) => (
                  <div key={i}>
                    <div className="truncate text-sm">{t.title}</div>
                    <div className="mt-0.5 flex flex-wrap items-center gap-1">
                      {(t.concepts ?? []).slice(0, 3).map(c => (
                        <span key={c} className="truncate rounded bg-elevated px-1.5 py-0.5 text-[10px] text-muted">{c}</span>
                      ))}
                      {t.time && <span className="ml-auto shrink-0 text-[10px] text-muted/70">{t.time.slice(5, 16)}</span>}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 监管预警 */}
          {mv.available && mv.count != null && (
            <div className="rounded-card border border-border bg-surface p-4">
              <PulseCardHeader icon={TriangleAlert} title="监管预警" date={mv.date} />
              {(mv.top?.length ?? 0) > 0 ? (
                <div className="space-y-1.5">
                  {(mv.top ?? []).slice(0, 5).map(m => (
                    <div key={`${m.rank}-${m.code}`} className="flex items-center justify-between gap-2 text-sm">
                      <span className="truncate">{m.name}</span>
                      <span className={cn('shrink-0 text-xs', priceColorClass(m.change_rate))}>
                        {fmtPct((m.change_rate ?? 0) / 100, 2)}
                      </span>
                    </div>
                  ))}
                  {(mv.count ?? 0) > (mv.top?.length ?? 0) && (
                    <div className="text-[11px] text-muted/70">共 {mv.count} 家触发监管标准</div>
                  )}
                </div>
              ) : (
                <div className="text-sm text-muted">当日无监管预警</div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

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

export default function Cockpit() {
  const overview = useQuery({
    queryKey: QK.cockpitOverview,
    queryFn: api.cockpitOverview,
    // 交易日盘中自动刷新 (60s); 休市日不轮询
    refetchInterval: query => (query.state.data?.session.trading_day ? 60_000 : false),
  })
  const d = overview.data
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [busy, setBusy] = useState<Record<string, { state: 'running' | 'done' | 'error'; message?: string }>>({})
  const [job, setJob] = useState<{ label: string; stage: string; progress: number } | null>(null)
  const [headerError, setHeaderError] = useState<string | null>(null)

  if (overview.isLoading) {
    return (
      <div className="min-h-[60vh] grid place-items-center">
        <Loader2 className="size-6 animate-spin text-muted" />
      </div>
    )
  }
  if (overview.isError || !d) {
    return (
      <div className="min-h-[60vh] grid place-items-center text-muted">
        驾驶舱数据加载失败 — 请检查后端 /api/cockpit/overview
      </div>
    )
  }

  const errCount = d.alerts.filter(a => a.level === 'error').length
  const warnCount = d.alerts.filter(a => a.level === 'warn').length
  const infoCount = d.alerts.filter(a => a.level === 'info').length

  const refresh = () => qc.invalidateQueries({ queryKey: QK.cockpitOverview })

  const generateDailyBrief = async () => {
    await api.dailyBriefGenerate()
    await qc.invalidateQueries({ queryKey: QK.cockpitOverview })
  }

  const runAlert = async (a: CockpitAlert) => {
    const kind = a.action as ActionKind | undefined
    if (!kind) return
    if (kind === 'evolution_run') { navigate('/workflow'); return }
    const key = `${kind}:${a.title}`
    setBusy(b => ({ ...b, [key]: { state: 'running' } }))
    try {
      const res = await runAction(kind, a.action_payload)
      if (res.jobId) {
        setJob({ label: a.action_label ?? a.title, stage: '准备中…', progress: 0 })
        await waitJob(res.jobId, (stage, pct) => setJob(j => (j ? { ...j, stage, progress: pct } : j)))
        setJob(null)
      }
      setBusy(b => ({ ...b, [key]: { state: 'done', message: res.message } }))
      await refresh()
    } catch (e) {
      setJob(null)
      setBusy(b => ({ ...b, [key]: { state: 'error', message: (e as Error)?.message ?? String(e) } }))
    }
  }

  const runPipeline = async () => {
    setHeaderError(null)
    setJob({ label: '盘后管道', stage: '启动中…', progress: 0 })
    try {
      const r = await api.pipelineRun()
      if (!r.reused) {
        await waitJob(r.job_id, (stage, pct) => setJob(j => (j ? { ...j, stage, progress: pct } : j)))
      }
      setJob(null)
      await refresh()
    } catch (e) {
      setJob(null)
      setHeaderError((e as Error)?.message ?? String(e))
    }
  }

  const pipelineBusy = job != null

  return (
    <div className="space-y-4">
      <PageHeader
        title="驾驶舱"
        subtitle="一屏聚合数据链路 / 市场环境 / 主线认证 / 工作流状态, 异常可一键处理"
        right={
          <div className="flex items-center gap-2">
            <Button
              variant="primary" size="sm"
              disabled={pipelineBusy}
              onClick={runPipeline}
            >
              {pipelineBusy
                ? <Loader2 className="size-4 animate-spin" />
                : <Play className="size-4" />}
              运行盘后管道
            </Button>
            <Button
              variant="ghost" size="sm"
              disabled={pipelineBusy}
              onClick={() => overview.refetch()}
            >
              <RefreshCw className={cn('size-4', overview.isFetching && 'animate-spin')} />
              刷新
            </Button>
          </div>
        }
      />

      {job && (
        <div className="rounded-card border border-accent/40 bg-accent/5 p-3">
          <div className="mb-1.5 flex items-center justify-between text-xs">
            <span className="flex items-center gap-2 font-medium text-accent">
              <Loader2 className="size-3.5 animate-spin" />
              {job.label} · {job.stage}
            </span>
            <span className="text-muted">{job.progress}%</span>
          </div>
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-elevated">
            <div className="h-full rounded-full bg-accent transition-all" style={{ width: `${job.progress}%` }} />
          </div>
        </div>
      )}

      {headerError && (
        <div className="flex items-center gap-2 rounded-card border border-red-500/30 bg-red-500/5 p-3 text-sm text-red-400">
          <CircleAlert className="size-4 shrink-0" /> 盘后管道执行失败：{headerError}
        </div>
      )}

      {/* 顶部状态灯 */}
      <div className={cn(
        'flex flex-wrap items-center gap-3 rounded-xl border p-4',
        d.status === 'ok'
          ? 'bg-emerald-500/10 border-emerald-500/30'
          : 'bg-red-500/10 border-red-500/30',
      )}>
        <div className={cn(
          'flex items-center gap-2 text-lg font-semibold',
          d.status === 'ok' ? 'text-emerald-400' : 'text-red-400',
        )}>
          {d.status === 'ok' ? <ShieldCheck className="size-5" /> : <CircleAlert className="size-5" />}
          {d.status === 'ok' ? '系统正常' : '存在异常, 请处理'}
        </div>
        <div className="flex items-center gap-4 text-sm text-muted">
          <span className="flex items-center gap-1"><span className="size-2 rounded-full bg-red-500" />{errCount} 错误</span>
          <span className="flex items-center gap-1"><span className="size-2 rounded-full bg-amber-500" />{warnCount} 警告</span>
          <span className="flex items-center gap-1"><span className="size-2 rounded-full bg-sky-500" />{infoCount} 提示</span>
          <span className="text-xs text-muted/70">截至 {d.as_of}</span>
        </div>
      </div>

      {/* 交易节点时间线 */}
      <SessionTimeline session={d.session} />

      {/* 市场脉搏 (zzshare 实时模块速览) */}
      <MarketPulse zz={d.zzshare} />

      <DailyBriefCard brief={d.daily_brief} onGenerate={generateDailyBrief} />

      <ResearchPanel />

      {/* DSA-style decision view, derived from the same cockpit facts. */}
      <div className="rounded-card border border-accent/30 bg-gradient-to-br from-accent/10 via-surface to-surface p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2 text-sm font-semibold">
            <Sparkles className="size-4 text-accent" /> 决策仪表盘
            <span className="rounded-full border border-border/70 bg-elevated/60 px-2 py-0.5 text-[10px] font-normal text-muted">
              基于当前市场事实
            </span>
          </div>
          <span className="text-[11px] text-muted">{d.decision_dashboard.as_of ?? d.as_of}</span>
        </div>
        {!d.decision_dashboard.available ? (
          <div className="text-sm text-muted">市场环境、主线或 Agent 报告尚未生成。</div>
        ) : (
          <div className="grid gap-3 md:grid-cols-[1.1fr_1fr_1fr]">
            <div className="rounded-lg border border-border/70 bg-surface/70 p-3">
              <div className="text-[11px] text-muted">市场状态</div>
              <div className="mt-1 flex items-baseline gap-2">
                <span className="text-2xl font-bold text-foreground">
                  {d.decision_dashboard.score == null ? '—' : d.decision_dashboard.score.toFixed(0)}
                </span>
                <span className="text-xs text-secondary">{d.decision_dashboard.state ?? '暂无状态'}</span>
              </div>
              <div className="mt-1 text-xs text-muted">阶段：{d.decision_dashboard.phase ?? '暂无'}</div>
              <div className="mt-2 text-xs text-secondary">工作流：{d.decision_dashboard.workflow_status}</div>
            </div>
            <div className="rounded-lg border border-border/70 bg-surface/70 p-3">
              <div className="mb-2 text-[11px] text-muted">主线与风险</div>
              {d.decision_dashboard.mainlines.length > 0 ? (
                <div className="space-y-1">
                  {d.decision_dashboard.mainlines.slice(0, 3).map(item => (
                    <div key={item.member} className="flex items-center justify-between gap-2 text-xs">
                      <span className="truncate">{item.member}</span>
                      <span className="shrink-0 text-muted">{item.score == null ? '—' : item.score.toFixed(1)} · {item.streak_days}日</span>
                    </div>
                  ))}
                </div>
              ) : <div className="text-xs text-muted">暂无主线认证</div>}
              {d.decision_dashboard.risk_alerts.length > 0 && (
                <div className="mt-2 truncate text-xs text-amber-400">
                  风险：{d.decision_dashboard.risk_alerts[0].title}
                </div>
              )}
            </div>
            <div className="rounded-lg border border-border/70 bg-surface/70 p-3">
              <div className="mb-2 text-[11px] text-muted">Agent 报告</div>
              {d.decision_dashboard.latest_agent_report ? (
                <button
                  className="text-left text-xs text-secondary transition-colors hover:text-accent"
                  onClick={() => navigate('/stock-analysis')}
                >
                  <div className="font-medium text-foreground">
                    {d.decision_dashboard.latest_agent_report.name || d.decision_dashboard.latest_agent_report.symbol}
                  </div>
                  <div className="mt-1 line-clamp-2">{d.decision_dashboard.latest_agent_report.summary || '已生成个股 Agent 报告，点击查看全文。'}</div>
                </button>
              ) : (
                <button className="text-xs text-accent hover:underline" onClick={() => navigate('/stock-analysis')}>
                  去个股分析生成 Agent 报告 →
                </button>
              )}
            </div>
          </div>
        )}
        {d.decision_dashboard.next_action && (
          <div className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-amber-400/20 bg-amber-400/5 px-3 py-2 text-xs">
            <span className="text-secondary">下一步：{d.decision_dashboard.next_action.title}</span>
            {d.decision_dashboard.next_action.action && (
              <Button
                variant="outline" size="xs" disabled={pipelineBusy}
                onClick={() => runAlert(d.decision_dashboard.next_action as CockpitAlert)}
              >
                {d.decision_dashboard.next_action.action_label ?? '处理'}
              </Button>
            )}
          </div>
        )}
      </div>

      {/* 数据链路 */}
      <div>
        <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-muted">
          <Database className="size-4" /> 数据链路
        </div>
        <div className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-7">
          {d.health.layers.map(l => <HealthDot key={l.key} layer={l} />)}
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        {/* 市场环境 */}
        <div className="rounded-card border border-border bg-surface p-4">
          <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
            <Gauge className="size-4" /> 市场环境
          </div>
          {d.regime.available ? (
            <div className="space-y-2">
              <div className="flex items-baseline gap-2">
                <span className="text-3xl font-bold">{d.regime.score?.toFixed?.(0) ?? d.regime.score}</span>
                <span className="text-xs text-muted">{d.regime.state_label ?? d.regime.state}</span>
              </div>
              <div className="flex flex-wrap gap-1.5">
                <span className="rounded-full bg-elevated px-2 py-0.5 text-xs">{d.regime.phase_label ?? d.regime.phase}</span>
                <span className="text-[11px] text-muted">{d.regime.date}</span>
              </div>
            </div>
          ) : (
            <p className="text-sm text-amber-400">{d.regime.detail ?? '环境分未生成'}</p>
          )}
        </div>

        {/* 主线认证 */}
        <div className="rounded-card border border-border bg-surface p-4">
          <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
            <Flame className="size-4" /> 主线认证
          </div>
          {d.mainline.available ? (
            <>
              <div className="mb-2 flex items-center gap-2 text-xs text-muted">
                金牌主线 {d.mainline.gold_count ?? 0} 个 · 截至 {d.mainline.as_of}
              </div>
              {d.mainline.leaders && d.mainline.leaders.length > 0 && (
                <div className="mb-3 rounded-lg border border-yellow-500/30 bg-yellow-500/10 p-2 text-sm">
                  <span className="text-muted">龙头身位 · </span>
                  <span className="font-semibold">{d.mainline.leaders[0].member}</span>
                  <span className="ml-1 font-mono text-yellow-400">{d.mainline.leaders[0].symbol}</span>
                  <span className="ml-2 text-xs text-muted">{d.mainline.leaders[0].max_boards} 连板</span>
                </div>
              )}
              <div className="space-y-1.5">
                {(d.mainline.items ?? []).slice(0, 5).map(i => (
                  <div key={i.member} className="flex items-center justify-between gap-2 text-sm">
                    <div className="flex min-w-0 items-center gap-1.5">
                      <LevelBadge level={i.level} />
                      <span className="truncate">{i.member}</span>
                    </div>
                    <div className="flex shrink-0 items-center gap-2 text-xs text-muted">
                      <span>{i.avg5_score.toFixed(1)}</span>
                      <span className="text-muted/60">连{i.streak_days}日</span>
                    </div>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <p className="text-sm text-amber-400">{d.mainline.detail ?? '主线时序未生成'}</p>
          )}
        </div>

        {/* 工作流 */}
        <div className="rounded-card border border-border bg-surface p-4">
          <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
            <Activity className="size-4" /> 工作流
          </div>
          <div className="space-y-2 text-sm">
            {d.workflow.latest_plan ? (
              <div className="flex items-center justify-between">
                <div>
                  <div className="font-medium">{d.workflow.latest_plan.plan_id}</div>
                  <div className="text-xs text-muted">{d.workflow.latest_plan.trade_date} · {d.workflow.latest_plan.entries} 标的</div>
                </div>
                <span className={cn(
                  'rounded-full px-2 py-0.5 text-xs font-medium',
                  d.workflow.latest_plan.status === 'reviewed'
                    ? 'bg-emerald-500/10 text-emerald-400'
                    : 'bg-amber-500/10 text-amber-400',
                )}>
                  {d.workflow.latest_plan.status === 'reviewed' ? '已复盘' : '待复盘'}
                </span>
              </div>
            ) : (
              <p className="text-muted">今日尚无计划</p>
            )}
            {d.workflow.latest_review && (
              <div className="border-t border-border pt-2 text-xs text-muted">
                最近复盘 {d.workflow.latest_review.trade_date} · 胜率{' '}
                <span className="text-bull">{d.workflow.latest_review.summary.win_rate?.toFixed?.(1) ?? '—'}%</span>
              </div>
            )}
            <div className="border-t border-border pt-2 text-xs text-muted">
              进化推荐 {d.workflow.recommendations_count ?? 0} · 已应用 {d.workflow.applied_evolution?.length ?? 0}
            </div>
          </div>
        </div>
      </div>

      {/* 提醒列表 */}
      <div>
        <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-muted">
          <TrendingUp className="size-4" /> 提醒
        </div>
        {d.alerts.length === 0 ? (
          <div className="rounded-card border border-border p-4 text-sm text-muted">暂无提醒, 一切正常</div>
        ) : (
          <div className="space-y-2">
            {d.alerts.map((a, i) => {
              const meta = LEVEL_META[a.level]
              const Icon = meta.icon
              const kind = a.action as ActionKind | undefined
              const key = kind ? `${kind}:${a.title}` : ''
              const st = key ? busy[key] : undefined
              const running = st?.state === 'running'
              return (
                <div key={i} className={cn('flex items-start gap-3 rounded-xl border p-3', meta.cls)}>
                  <Icon className="mt-0.5 size-4 shrink-0" />
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium">{a.title}</div>
                    <div className="text-xs opacity-80">{a.detail}</div>
                    {st?.message && (
                      <div className={cn('mt-1 text-xs font-medium',
                        st.state === 'error' ? 'text-red-400' : 'text-emerald-400')}>
                        {st.state === 'error' ? '✗ ' : '✓ '}{st.message}
                      </div>
                    )}
                  </div>
                  {kind && (
                    <div className="shrink-0">
                      <Button
                        variant={a.level === 'error' ? 'primary' : 'outline'}
                        size="xs"
                        disabled={running || pipelineBusy}
                        onClick={() => runAlert(a)}
                      >
                        {running
                          ? <Loader2 className="size-3.5 animate-spin" />
                          : <Play className="size-3.5" />}
                        {a.action_label ?? '处理'}
                      </Button>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}

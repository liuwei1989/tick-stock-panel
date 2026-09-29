/**
 * 工作流闭环页 — 策略发现 → 盘前计划 → 复盘 全流程串通。
 *
 * 数据来源:
 *  - 总览:      GET  /api/workflow/overview
 *  - 计划列表:   GET  /api/workflow/plans
 *  - 生成计划:   POST /api/workflow/plan/generate  (进化推荐扫描 + 自选补充池)
 *  - 程序化复盘: POST /api/workflow/plan/{id}/review (对照计划逐标的算命中/盈亏/胜率)
 *
 * 布局: 顶部闭环状态条 → 工具栏(生成计划) → 左计划列表 / 右详情(计划标的表 + 复盘结果)。
 */
import { useMemo, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  RefreshCw, Loader2, ClipboardList, CheckCircle2, Target,
  Sparkles, FileText, Plus,
} from 'lucide-react'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { cn } from '@/lib/cn'
import { fmtPct } from '@/lib/format'
import { PageHeader } from '@/components/PageHeader'
import { toast } from '@/components/Toast'

function exitLabel(k: string): string {
  if (k === 'take_profit') return '止盈'
  if (k === 'stop_loss') return '止损'
  if (k === 'max_hold') return '到期'
  return '收盘'
}

function pctClass(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v) || v === 0) return 'text-muted'
  return v > 0 ? 'text-bull' : 'text-bear'
}

function StatusBadge({ status }: { status: string }) {
  const reviewed = status === 'reviewed'
  return (
    <span className={cn(
      'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium',
      reviewed ? 'bg-emerald-500/10 text-emerald-400' : 'bg-amber-500/10 text-amber-400',
    )}>
      {reviewed ? <CheckCircle2 className="size-3" /> : <Target className="size-3" />}
      {reviewed ? '已复盘' : '待复盘'}
    </span>
  )
}

export default function Workflow() {
  const qc = useQueryClient()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [reviewOpen, setReviewOpen] = useState(false)
  const [maxEntries, setMaxEntries] = useState(20)
  const [trackDays, setTrackDays] = useState(5)

  const overview = useQuery({ queryKey: QK.workflowOverview, queryFn: api.workflowOverview })
  const plansQ = useQuery({ queryKey: QK.workflowPlans(), queryFn: () => api.workflowPlans() })
  const selectedQ = useQuery({
    queryKey: QK.workflowPlan(selectedId ?? ''),
    queryFn: () => api.workflowPlan(selectedId!),
    enabled: !!selectedId,
  })
  const plan = selectedQ.data

  const generate = useMutation({
    mutationFn: () => api.workflowGeneratePlan({ max_entries: maxEntries, use_evolution: true }),
    onSuccess: (r) => {
      if (!r.ok) { toast(r.error ?? '生成失败', 'error'); return }
      toast(`计划 ${r.plan_id} 已生成 (${r.entries.length} 个标的)`, 'success')
      setSelectedId(r.plan_id)
      setReviewOpen(false)
      qc.invalidateQueries({ queryKey: QK.workflowPlans() })
      qc.invalidateQueries({ queryKey: QK.workflowOverview })
    },
  })

  const review = useMutation({
    mutationFn: (planId: string) => api.workflowReviewPlan(planId, { track_days: trackDays }),
    onSuccess: (r) => {
      if (!r.ok) { toast(r.error ?? '复盘失败', 'error'); return }
      toast(r.already_reviewed ? '该计划已复盘过, 展示既有结果' : '复盘完成', 'success')
      setReviewOpen(true)
      qc.invalidateQueries({ queryKey: QK.workflowPlans() })
      qc.invalidateQueries({ queryKey: QK.workflowOverview })
    },
  })

  const plans = plansQ.data?.plans ?? []
  const latestPlan = useMemo(() => plans.find((p) => p.plan_id === selectedId) ?? plans[0], [plans, selectedId])

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-4">
      <PageHeader
        title="工作流闭环"
        subtitle="策略发现 → 盘前计划 → 复盘"
        right={
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-1 rounded-lg border border-border bg-surface px-2 py-1">
              <span className="text-xs text-muted">上限</span>
              <input
                type="number" min={1} max={200} value={maxEntries}
                onChange={(e) => setMaxEntries(Math.max(1, Math.min(200, Number(e.target.value) || 20)))}
                className="w-14 bg-transparent text-sm outline-none"
              />
            </div>
            <button
              onClick={() => generate.mutate()}
              disabled={generate.isPending}
              className="flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-accent/90 disabled:opacity-50"
            >
              {generate.isPending ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />}
              生成今日计划
            </button>
            <button
              onClick={() => { qc.invalidateQueries({ queryKey: QK.workflowOverview }); qc.invalidateQueries({ queryKey: QK.workflowPlans() }) }}
              className="flex items-center gap-1 rounded-lg border border-border px-2 py-1.5 text-sm hover:bg-elevated"
            >
              <RefreshCw className="size-3.5" /> 刷新
            </button>
          </div>
        }
      />

      {/* 闭环状态条 */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <StatCard label="最新计划" value={overview.data?.latest_plan?.plan_id ?? '—'}
          sub={overview.data?.latest_plan ? `${overview.data.latest_plan.trade_date} · ${overview.data.latest_plan.entries} 标的` : '尚未生成'} />
        <StatCard label="计划状态" value={overview.data?.latest_plan?.status === 'reviewed' ? '已复盘' : (overview.data?.latest_plan ? '待复盘' : '—')}
          tone={overview.data?.latest_plan?.status === 'reviewed' ? 'good' : 'warn'} />
        <StatCard label="最新复盘胜率" value={overview.data?.latest_review ? fmtPct(overview.data.latest_review.summary.win_rate * 100) + '%' : '—'}
          sub={overview.data?.latest_review ? `触发 ${overview.data.latest_review.summary.triggered}/${overview.data.latest_review.summary.planned} · 均收益 ${fmtPct(overview.data.latest_review.summary.avg_pnl_pct * 100)}%` : '无复盘'} />
        <StatCard label="进化已应用" value={String(overview.data?.applied_evolution.length ?? 0)}
          sub={(overview.data?.applied_evolution ?? []).map((e) => e.strategy_id).join(', ') || '无'} />
        <StatCard label="反馈沉淀" value={`${overview.data?.recommendations_count ?? 0} 推荐 / ${overview.data?.feedback_count ?? 0} 反馈`} />
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 lg:grid-cols-[300px_1fr]">
        {/* 左: 计划列表 */}
        <div className="flex min-h-0 flex-col rounded-xl border border-border bg-surface">
          <div className="border-b border-border px-3 py-2 text-xs font-semibold uppercase tracking-wide text-muted">
            盘前计划 ({plans.length})
          </div>
          <div className="min-h-0 flex-1 space-y-1 overflow-y-auto p-2">
            {plans.length === 0 && (
              <div className="flex flex-col items-center gap-2 px-4 py-10 text-center text-sm text-muted">
                <ClipboardList className="size-8 opacity-40" />
                还没有计划 — 点右上角「生成今日计划」
              </div>
            )}
            {plans.map((p) => (
              <button
                key={p.plan_id}
                onClick={() => { setSelectedId(p.plan_id); setReviewOpen(false) }}
                className={cn(
                  'flex w-full flex-col gap-1 rounded-lg border px-3 py-2 text-left transition',
                  p.plan_id === (plan?.plan_id ?? latestPlan?.plan_id)
                    ? 'border-accent/50 bg-accent/10'
                    : 'border-border hover:bg-elevated',
                )}
              >
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium">{p.plan_id}</span>
                  <StatusBadge status={p.status} />
                </div>
                <div className="flex items-center justify-between text-xs text-muted">
                  <span>{p.trade_date}</span>
                  <span>{p.entries.length} 标的</span>
                </div>
              </button>
            ))}
          </div>
        </div>

        {/* 右: 详情 */}
        <div className="min-h-0 overflow-y-auto rounded-xl border border-border bg-surface p-4">
          {!plan ? (
            <div className="flex h-full flex-col items-center justify-center gap-3 text-center text-sm text-muted">
              <Sparkles className="size-10 opacity-40" />
              <div>
                <p className="text-[16px] leading-6 font-medium text-foreground">选择左侧计划查看详情</p>
                <p className="mt-1">或先生成计划 — 将用进化推荐策略扫描 + 自选股补充池</p>
              </div>
            </div>
          ) : (
            <>
              {/* 计划头 */}
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <h2 className="text-[16px] leading-6 font-semibold">{plan.plan_id}</h2>
                  <p className="text-xs text-muted">交易日 {plan.trade_date} · 生成于 {plan.created_at.slice(5, 16)}</p>
                </div>
                <div className="flex items-center gap-2">
                  {plan.regime?.phase ? (
                    <span className="rounded-full bg-elevated px-2 py-0.5 text-xs">
                      环境 {String(plan.regime.state ?? '')} / {String(plan.regime.phase ?? '')}
                      {plan.regime.score != null ? ` · 分 ${plan.regime.score}` : ''}
                    </span>
                  ) : null}
                  {plan.status === 'reviewed' && plan.review_id ? (
                    <button
                      onClick={() => setReviewOpen(!reviewOpen)}
                      className="flex items-center gap-1 rounded-lg border border-emerald-500/40 px-2.5 py-1 text-xs font-medium text-emerald-400 hover:bg-emerald-500/10"
                    >
                      <FileText className="size-3.5" /> {reviewOpen ? '收起复盘' : '查看复盘'}
                    </button>
                  ) : (
                    <div className="flex items-center gap-1">
                      <select
                        value={trackDays}
                        onChange={(e) => setTrackDays(Number(e.target.value))}
                        className="rounded-lg border border-border bg-surface px-1.5 py-1 text-xs outline-none"
                        title="持有期跟踪天数 (交易日, 含执行日)"
                      >
                        <option value={1}>当日</option>
                        <option value={5}>5日持有</option>
                        <option value={10}>10日持有</option>
                      </select>
                      <button
                        onClick={() => review.mutate(plan.plan_id)}
                        disabled={review.isPending}
                        className="flex items-center gap-1 rounded-lg bg-emerald-600/90 px-2.5 py-1 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
                      >
                        {review.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <Target className="size-3.5" />}
                        复盘
                      </button>
                    </div>
                  )}
                </div>
              </div>

              {/* 策略来源 */}
              <div className="mt-2 flex flex-wrap gap-1.5">
                {(plan.source?.strategies ?? []).map((s) => (
                  <span key={s.strategy_id} className="rounded-md bg-elevated px-1.5 py-0.5 text-[11px] text-muted">
                    {s.name || s.strategy_id} <span className="opacity-60">· {s.source}</span>
                  </span>
                ))}
              </div>

              {/* 复盘结果 */}
              {plan.status === 'reviewed' && plan.review_id && reviewOpen && <ReviewPanel planId={plan.plan_id} />}

              {/* 标的表 */}
              <div className="mt-4 overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border text-left text-xs text-muted">
                      <th className="py-2 pr-2 font-medium">标的</th>
                      <th className="py-2 pr-2 font-medium">策略</th>
                      <th className="py-2 pr-2 font-medium">加入信号</th>
                      <th className="py-2 pr-2 font-medium">退出信号</th>
                      <th className="py-2 pr-2 text-right font-medium">分</th>
                      <th className="py-2 pr-2 text-right font-medium">参考价</th>
                      <th className="py-2 pr-2 text-right font-medium">触发区间</th>
                      <th className="py-2 pr-2 text-right font-medium">止盈/止损</th>
                      <th className="py-2 pr-2 font-medium">来源</th>
                      <th className="py-2 font-medium">备注信号</th>
                    </tr>
                  </thead>
                  <tbody>
                    {plan.entries.map((e) => (
                      <tr key={e.symbol} className="border-b border-border/60 hover:bg-elevated/40">
                        <td className="py-2 pr-2 font-medium">{e.symbol}</td>
                        <td className="py-2 pr-2 text-xs text-muted">{e.strategy_name || e.strategy_id}</td>
                        <td className="max-w-[140px] truncate py-2 pr-2 text-xs text-emerald-400/90" title={e.entry_signal}>
                          {e.entry_signal || '—'}
                        </td>
                        <td className="max-w-[140px] truncate py-2 pr-2 text-xs text-rose-400/80" title={e.exit_signal}>
                          {e.exit_signal || '—'}
                        </td>
                        <td className="py-2 pr-2 text-right">{e.score != null ? e.score.toFixed(1) : '—'}</td>
                        <td className="py-2 pr-2 text-right">{e.reference_price ?? '—'}</td>
                        <td className="py-2 pr-2 text-right text-xs text-muted">
                          {e.entry_low != null && e.entry_high != null ? `${e.entry_low.toFixed(2)} ~ ${e.entry_high.toFixed(2)}` : '—'}
                        </td>
                        <td className="py-2 pr-2 text-right text-xs text-muted">
                          {e.take_profit != null || e.stop_loss != null
                            ? `${e.take_profit != null ? '盈' + e.take_profit.toFixed(2) : ''}${e.take_profit != null && e.stop_loss != null ? ' / ' : ''}${e.stop_loss != null ? '损' + e.stop_loss.toFixed(2) : ''}`
                            : '—'}
                        </td>
                        <td className="py-2 pr-2 text-xs">
                          <span className={cn(
                            'rounded px-1.5 py-0.5 text-[11px]',
                            e.source === 'watchlist' ? 'bg-sky-500/10 text-sky-400' : 'bg-violet-500/10 text-violet-400',
                          )}>
                            {e.source === 'watchlist' ? '自选池' : '进化扫描'}
                          </span>
                        </td>
                        <td className="max-w-[220px] truncate py-2 text-xs text-muted" title={e.signal}>
                          {e.signal || '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

/** 复盘结果面板: 汇总 + 命中矩阵 + 策略反馈 */
function ReviewPanel({ planId }: { planId: string }) {
  const reviews = useQuery({
    queryKey: QK.workflowReviews(),
    queryFn: () => api.workflowReviews(),
  })
  const review = reviews.data?.reviews.find((r) => r.plan_id === planId)
  if (!review) return null
  const s = review.summary
  return (
    <div className="mt-4 rounded-xl border border-emerald-500/20 bg-emerald-500/[0.04] p-4">
      <div className="flex items-center gap-2 text-sm font-semibold text-emerald-400">
        <CheckCircle2 className="size-4" /> 复盘结果 {review.review_id} <span className="text-xs text-muted">· {review.trade_date}</span>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2 md:grid-cols-5">
        <MiniStat label="触发" value={`${s.triggered}/${s.planned}`} />
        <MiniStat label="胜率" value={fmtPct(s.win_rate * 100) + '%'} tone={s.win_rate >= 0.5 ? 'good' : 'bad'} />
        <MiniStat label="平均收益" value={fmtPct(s.avg_pnl_pct * 100) + '%'} tone={s.avg_pnl_pct > 0 ? 'good' : 'bad'} />
        <MiniStat label="平均持有" value={s.avg_hold_days != null ? s.avg_hold_days + '日' : '—'} />
        <MiniStat label="平均最佳" value={fmtPct(s.avg_best_pnl_pct * 100) + '%'} />
        <MiniStat label="退出方式" value={Object.entries(s.exits ?? {}).map(([k, v]) => `${exitLabel(k)}×${v}`).join(' ') || '—'} />
        <MiniStat label="最优/最差" value={s.best_symbol ?? '—'} sub={s.worst_symbol ?? '—'} />
      </div>
      {/* 逐标的命中 */}
      <div className="mt-3 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs text-muted">
              <th className="py-1.5 pr-2 font-medium">标的</th>
              <th className="py-1.5 pr-2 text-right font-medium">开盘</th>
              <th className="py-1.5 pr-2 text-right font-medium">最高</th>
              <th className="py-1.5 pr-2 text-right font-medium">收盘</th>
              <th className="py-1.5 pr-2 text-right font-medium">成交价</th>
              <th className="py-1.5 pr-2 text-right font-medium">退出价</th>
              <th className="py-1.5 pr-2 font-medium">退出日</th>
              <th className="py-1.5 pr-2 text-right font-medium">持有</th>
              <th className="py-1.5 pr-2 font-medium">退出方式</th>
              <th className="py-1.5 pr-2 text-right font-medium">收益</th>
              <th className="py-1.5 pr-2 text-right font-medium">最佳</th>
              <th className="py-1.5 font-medium">结果</th>
            </tr>
          </thead>
          <tbody>
            {review.results.map((r) => (
              <tr key={r.symbol} className="border-b border-border/60">
                <td className="py-1.5 pr-2 font-medium">{r.symbol}</td>
                <td className="py-1.5 pr-2 text-right text-xs text-muted">{r.open ?? '—'}</td>
                <td className="py-1.5 pr-2 text-right text-xs text-muted">{r.high ?? '—'}</td>
                <td className="py-1.5 pr-2 text-right text-xs text-muted">{r.close ?? '—'}</td>
                <td className="py-1.5 pr-2 text-right text-xs text-muted">{r.fill_price ?? '—'}</td>
                <td className="py-1.5 pr-2 text-right text-xs text-muted">{r.exit_price ?? '—'}</td>
                <td className="py-1.5 pr-2 text-xs text-muted">{r.exit_date ?? '—'}</td>
                <td className="py-1.5 pr-2 text-right text-xs text-muted">{r.hold_days != null ? r.hold_days + '日' : '—'}</td>
                <td className="py-1.5 pr-2 text-xs">
                  {r.exit_reason === 'take_profit' && <span className="text-emerald-400">止盈</span>}
                  {r.exit_reason === 'stop_loss' && <span className="text-rose-400">止损</span>}
                  {r.exit_reason === 'close' && <span className="text-muted">收盘</span>}
                  {r.exit_reason === 'max_hold' && <span className="text-amber-400">到期</span>}
                  {!r.exit_reason && <span className="text-muted">—</span>}
                </td>
                <td className={cn('py-1.5 pr-2 text-right', pctClass(r.pnl_pct))}>
                  {r.pnl_pct != null ? fmtPct(r.pnl_pct * 100) + '%' : '—'}
                </td>
                <td className={cn('py-1.5 pr-2 text-right text-xs', pctClass(r.best_pnl_pct))}>
                  {r.best_pnl_pct != null ? fmtPct(r.best_pnl_pct * 100) + '%' : '—'}
                </td>
                <td className="py-1.5">
                  {r.hit === true && <span className="text-emerald-400">触发</span>}
                  {r.hit === false && <span className="text-muted">未触发</span>}
                  {r.hit === null && <span className="text-amber-400/80">{r.note}</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {/* 策略反馈 */}
      {review.strategy_feedback.length > 0 && (
        <div className="mt-3">
          <div className="text-xs font-semibold uppercase tracking-wide text-muted">策略表现反馈 (回写进化)</div>
          <div className="mt-1.5 flex flex-wrap gap-2">
            {review.strategy_feedback.map((f) => (
              <div key={f.strategy_id} className="rounded-lg border border-border bg-surface px-2.5 py-1.5 text-xs">
                <span className="font-medium">{f.strategy_id}</span>
                <span className="mx-1.5 text-muted">触发 {f.triggered}/{f.planned}</span>
                <span className={cn('font-medium', f.win_rate >= 0.5 ? 'text-emerald-400' : 'text-rose-400')}>
                  胜率 {fmtPct(f.win_rate * 100)}%
                </span>
                <span className={cn('ml-1.5', pctClass(f.avg_pnl_pct))}>均收 {fmtPct(f.avg_pnl_pct * 100)}%</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function StatCard({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: 'good' | 'warn' }) {
  return (
    <div className="rounded-xl border border-border bg-surface p-3">
      <div className="text-xs text-muted">{label}</div>
      <div className={cn(
        'mt-1 truncate text-[16px] leading-6 font-semibold',
        tone === 'good' ? 'text-emerald-400' : tone === 'warn' ? 'text-amber-400' : 'text-foreground',
      )}>{value}</div>
      {sub && <div className="mt-0.5 truncate text-[11px] text-muted">{sub}</div>}
    </div>
  )
}

function MiniStat({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: 'good' | 'bad' }) {
  return (
    <div className="rounded-lg border border-border/80 bg-surface px-2.5 py-1.5">
      <div className="text-[11px] text-muted">{label}</div>
      <div className={cn('text-sm font-semibold', tone === 'good' ? 'text-emerald-400' : tone === 'bad' ? 'text-rose-400' : 'text-foreground')}>
        {value}
      </div>
      {sub && <div className="text-[11px] text-muted">{sub}</div>}
    </div>
  )
}

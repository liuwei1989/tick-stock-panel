/**
 * 策略进化管理页 — 策略发现环节。
 *
 * 数据来源:
 *  - 策略列表:     GET  /api/strategies
 *  - 运行进化:     POST /api/evolution/run       (walk-forward 候选评估 + 门槛推荐)
 *  - 推荐记录:     GET  /api/evolution/recommendations
 *  - 已应用参数:   GET  /api/evolution/applied
 *  - 应用/撤销:    POST /api/evolution/recommendations/{run_id}/apply
 *                  DELETE /api/evolution/recommendations/applied/{strategy_id}
 *  - 环境门控:     GET  /api/evolution/env-gate/suggestion
 *
 * 布局: 顶部环境门控 → 运行进化面板(参数+日志+结果) → 已应用参数 → 推荐记录。
 */
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  RefreshCw, Loader2, FlaskConical, CheckCircle2, XCircle,
  Sparkles, ShieldCheck, ChevronDown, ChevronUp, Undo2,
} from 'lucide-react'
import { api, EvolutionRecord, EvolutionCandidate } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { cn } from '@/lib/cn'
import { fmtPct } from '@/lib/format'
import { PageHeader } from '@/components/PageHeader'
import { toast } from '@/components/Toast'

function pct(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—'
  return fmtPct(v * 100) + '%'
}

function num(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v)) return '—'
  return v.toFixed(digits)
}

function toneClass(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v) || v === 0) return 'text-muted'
  return v > 0 ? 'text-emerald-400' : 'text-rose-400'
}

function RunStatus({ status }: { status: string }) {
  const ok = status === 'recommended'
  return (
    <span className={cn(
      'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium',
      ok ? 'bg-emerald-500/10 text-emerald-400' : 'bg-zinc-700/30 text-muted',
    )}>
      {ok ? <Sparkles className="size-3" /> : <XCircle className="size-3" />}
      {ok ? '有推荐' : '无改进'}
    </span>
  )
}

function Stat({ label, value, cls }: { label: string; value: string; cls?: string }) {
  return (
    <div className="min-w-[92px] rounded-lg border border-zinc-800/80 bg-zinc-900/50 px-2.5 py-1.5">
      <div className="text-[11px] text-muted">{label}</div>
      <div className={cn('truncate text-sm font-semibold', cls ?? 'text-foreground')}>{value}</div>
    </div>
  )
}

function CandidateCard({ name, score, validate, wf, baseline, applied }: {
  name: string
  score: number | null
  validate: EvolutionCandidate['validate']
  wf: EvolutionCandidate['walk_forward']
  baseline: EvolutionRecord['baseline']
  applied: boolean
}) {
  return (
    <div className={cn(
      'flex flex-col gap-2 rounded-xl border p-3',
      applied ? 'border-emerald-500/40 bg-emerald-500/5' : 'border-zinc-800 bg-zinc-900/40',
    )}>
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium">{name}</span>
        {applied && <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/15 px-2 py-0.5 text-[11px] font-medium text-emerald-400">
          <CheckCircle2 className="size-3" /> 已应用
        </span>}
      </div>
      <div className="flex flex-wrap gap-2">
        <Stat label="综合分" value={num(score)} />
        <Stat label="验证收益" value={pct(validate?.total_return)} cls={toneClass(validate?.total_return)} />
        <Stat label="胜率" value={pct(validate?.win_rate)} />
        <Stat label="回撤" value={pct(validate?.max_drawdown)} cls="text-amber-400" />
        <Stat label="盈亏比" value={num(validate?.profit_factor)} />
        <Stat label="交易数" value={String(validate?.n_trades ?? '—')} />
        <Stat label="稳定分" value={num(wf?.stability_score)} />
        <Stat label="正向窗口" value={wf ? `${wf.positive_windows}/${wf.active_windows}` : '—'} />
      </div>
      {baseline && (
        <div className="text-[11px] text-muted">
          对比基线: 收益 {pct(baseline.validate.total_return)} → <span className={toneClass(validate?.total_return)}>{pct(validate?.total_return)}</span>
          {' '}· 胜率 {pct(baseline.validate.win_rate)} → {pct(validate?.win_rate)}
          {' '}· 回撤 {pct(baseline.validate.max_drawdown)} → {pct(validate?.max_drawdown)}
        </div>
      )}
      {Object.keys(validate?.strategy_params ?? {}).length > 0 && (
        <div className="truncate text-[11px] text-muted">
          参数: {JSON.stringify((validate as any)?.strategy_params ?? {})}
        </div>
      )}
    </div>
  )
}

export default function Evolution() {
  const qc = useQueryClient()

  // 运行表单
  const [strategyId, setStrategyId] = useState('')
  const [trainDays, setTrainDays] = useState(180)
  const [validateDays, setValidateDays] = useState(30)
  const [windows, setWindows] = useState(3)
  const [candidates, setCandidates] = useState(12)
  const [maxPositions, setMaxPositions] = useState(5)
  const [holdingDays, setHoldingDays] = useState(5)
  const [iterative, setIterative] = useState(true)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [lastRun, setLastRun] = useState<(EvolutionRecord & { progress?: string[] }) | null>(null)

  const strategiesQ = useQuery({ queryKey: QK.screenerStrategies('stock', '1d'), queryFn: () => api.screenerStrategies(undefined, '1d') })
  const strategies = strategiesQ.data?.presets ?? []
  const envGate = useQuery({ queryKey: QK.evolutionEnvGate, queryFn: api.evolutionEnvGate })
  const recsQ = useQuery({ queryKey: QK.evolutionRecommendations, queryFn: () => api.evolutionRecommendations(20) })
  const appliedQ = useQuery({ queryKey: QK.evolutionApplied, queryFn: api.evolutionApplied })
  const applied = appliedQ.data?.applied ?? {}

  const run = useMutation({
    mutationFn: () => api.evolutionRun({
      strategy_id: strategyId,
      train_days: trainDays,
      validate_days: validateDays,
      walk_forward_windows: windows,
      max_candidates: candidates,
      min_validation_trades: 8,
      min_positive_windows: 3,
      min_positive_window_ratio: 0.5,
      min_stability_score: 0.5,
      max_positions: maxPositions,
      holding_days: holdingDays,
      iterative_seeds: iterative,
    }),
    onSuccess: (r) => {
      if (!r.run_id) { toast('进化未产出结果 (见日志)', 'error'); return }
      setLastRun(r)
      toast(`进化完成: ${r.status === 'recommended' ? '有推荐候选' : '无改进'}`, r.status === 'recommended' ? 'success' : 'error')
      qc.invalidateQueries({ queryKey: QK.evolutionRecommendations })
    },
    onError: (e: Error) => toast(e.message || '进化失败', 'error'),
  })

  const apply = useMutation({
    mutationFn: (runId: string) => api.evolutionApply(runId),
    onSuccess: (r) => {
      toast(`已应用 ${r.strategy_id} 的进化参数`, 'success')
      qc.invalidateQueries({ queryKey: QK.evolutionApplied })
      qc.invalidateQueries({ queryKey: QK.evolutionRecommendations })
    },
    onError: (e: Error) => toast(e.message || '应用失败', 'error'),
  })

  const clear = useMutation({
    mutationFn: (sid: string) => api.evolutionClearApplied(sid),
    onSuccess: () => {
      toast('已撤销, 恢复策略默认参数', 'success')
      qc.invalidateQueries({ queryKey: QK.evolutionApplied })
    },
    onError: (e: Error) => toast(e.message || '撤销失败', 'error'),
  })

  const g = envGate.data
  const records = recsQ.data?.records ?? []

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-4">
      <PageHeader
        title="策略进化"
        subtitle="walk-forward 进化 → 推荐 → 环境门控"
        right={
          <button
            onClick={() => {
              qc.invalidateQueries({ queryKey: QK.evolutionEnvGate })
              qc.invalidateQueries({ queryKey: QK.evolutionRecommendations })
              qc.invalidateQueries({ queryKey: QK.evolutionApplied })
            }}
            className="flex items-center gap-1 rounded-lg border border-zinc-800 px-2 py-1.5 text-sm hover:bg-zinc-900"
          >
            <RefreshCw className="size-3.5" /> 刷新
          </button>
        }
      />

      {/* 环境门控 */}
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
        <div className="mb-3 flex items-center gap-2">
          <ShieldCheck className="size-4 text-primary" />
          <span className="text-sm font-semibold">环境门控建议</span>
          {g?.available && g.date && <span className="text-xs text-muted">· {g.date}</span>}
        </div>
        {!g ? (
          <div className="flex items-center gap-2 text-sm text-muted">
            <Loader2 className="size-4 animate-spin" /> 读取市场环境…
          </div>
        ) : !g.available ? (
          <div className="text-sm text-muted">{g.detail ?? '无 regime 数据, 无法给出环境建议'}</div>
        ) : (
          <div className="flex flex-wrap items-center gap-4">
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted">环境分</span>
              <span className={cn('text-lg font-bold', toneClass(g.regime?.score))}>{num(g.regime?.score, 0)}</span>
              <span className="text-xs text-muted">{g.regime?.state_label ?? g.regime?.state ?? ''} · {g.regime?.phase_label ?? g.regime?.phase ?? ''}</span>
            </div>
            <div className="h-8 w-px bg-zinc-800" />
            <div className="flex flex-wrap gap-2">
              <Stat label="基线 min_score" value={String(g.default_params.min_score)} />
              <Stat label="建议 min_score" value={String(g.applied_params.min_score)} cls="text-primary" />
              <Stat label="基线 max_results" value={String(g.default_params.max_results)} />
              <Stat label="建议 max_results" value={String(g.applied_params.max_results)} cls="text-primary" />
            </div>
            <div className="w-full text-xs text-muted">{g.suggestion}</div>
          </div>
        )}
      </div>

      {/* 运行进化 */}
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
        <div className="mb-3 flex items-center gap-2">
          <FlaskConical className="size-4 text-primary" />
          <span className="text-sm font-semibold">运行一轮进化</span>
          <span className="text-xs text-muted">候选生成 + walk-forward 评估 + 门槛筛选, 持久化为推荐记录</span>
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1">
            <span className="text-[11px] text-muted">策略</span>
            <select
              value={strategyId}
              onChange={(e) => setStrategyId(e.target.value)}
              className="w-44 rounded-lg border border-zinc-800 bg-zinc-900 px-2 py-1.5 text-sm outline-none"
            >
              <option value="">选择策略…</option>
              {strategies.map((s) => (
                <option key={s.id} value={s.id}>{s.name} ({s.id})</option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[11px] text-muted">训练天数</span>
            <input type="number" min={60} max={1000} value={trainDays}
              onChange={(e) => setTrainDays(Number(e.target.value) || 180)}
              className="w-20 rounded-lg border border-zinc-800 bg-zinc-900 px-2 py-1.5 text-sm outline-none" />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[11px] text-muted">验证天数</span>
            <input type="number" min={10} max={300} value={validateDays}
              onChange={(e) => setValidateDays(Number(e.target.value) || 30)}
              className="w-20 rounded-lg border border-zinc-800 bg-zinc-900 px-2 py-1.5 text-sm outline-none" />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[11px] text-muted">窗口数</span>
            <input type="number" min={1} max={10} value={windows}
              onChange={(e) => setWindows(Number(e.target.value) || 3)}
              className="w-16 rounded-lg border border-zinc-800 bg-zinc-900 px-2 py-1.5 text-sm outline-none" />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[11px] text-muted">候选数</span>
            <input type="number" min={2} max={40} value={candidates}
              onChange={(e) => setCandidates(Number(e.target.value) || 12)}
              className="w-16 rounded-lg border border-zinc-800 bg-zinc-900 px-2 py-1.5 text-sm outline-none" />
          </label>
          <button
            onClick={() => setShowAdvanced(!showAdvanced)}
            className="flex items-center gap-1 rounded-lg border border-zinc-800 px-2 py-1.5 text-xs text-muted hover:bg-zinc-900"
          >
            {showAdvanced ? <ChevronUp className="size-3.5" /> : <ChevronDown className="size-3.5" />}
            高级
          </button>
          <button
            onClick={() => run.mutate()}
            disabled={run.isPending || !strategyId}
            className="flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
          >
            {run.isPending ? <Loader2 className="size-4 animate-spin" /> : <FlaskConical className="size-4" />}
            {run.isPending ? '进化中…' : '运行进化'}
          </button>
        </div>
        {showAdvanced && (
          <div className="mt-3 flex flex-wrap items-end gap-3 border-t border-zinc-800 pt-3">
            <label className="flex flex-col gap-1">
              <span className="text-[11px] text-muted">持仓数上限</span>
              <input type="number" min={1} max={20} value={maxPositions}
                onChange={(e) => setMaxPositions(Number(e.target.value) || 5)}
                className="w-20 rounded-lg border border-zinc-800 bg-zinc-900 px-2 py-1.5 text-sm outline-none" />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-[11px] text-muted">持有天数</span>
              <input type="number" min={1} max={60} value={holdingDays}
                onChange={(e) => setHoldingDays(Number(e.target.value) || 5)}
                className="w-20 rounded-lg border border-zinc-800 bg-zinc-900 px-2 py-1.5 text-sm outline-none" />
            </label>
            <label className="flex items-center gap-1.5 text-sm">
              <input type="checkbox" checked={iterative}
                onChange={(e) => setIterative(e.target.checked)}
                className="size-4 accent-primary" />
              迭代种子 (复用上轮优秀参数)
            </label>
          </div>
        )}
        {lastRun && (
          <div className="mt-3 rounded-lg border border-zinc-800 bg-zinc-950/60 p-3">
            <div className="mb-2 flex items-center gap-2">
              <RunStatus status={lastRun.status} />
              <span className="text-xs text-muted">{lastRun.run_id} · {lastRun.created_at}</span>
            </div>
            <div className="flex flex-wrap gap-2">
              {lastRun.baseline && (
                <div className="w-full">
                  <div className="mb-1 text-[11px] text-muted">基线 ({lastRun.baseline.name})</div>
                  <CandidateCard name="当前策略" score={lastRun.baseline.score}
                    validate={lastRun.baseline.validate} wf={lastRun.baseline.walk_forward} baseline={null} applied={false} />
                </div>
              )}
              {lastRun.recommendation ? (
                <div className="w-full">
                  <div className="mb-1 text-[11px] text-muted">推荐候选</div>
                  <CandidateCard name={lastRun.recommendation.name} score={lastRun.recommendation.score}
                    validate={lastRun.recommendation.validate} wf={lastRun.recommendation.walk_forward}
                    baseline={lastRun.baseline} applied={!!applied[lastRun.strategy_id]} />
                  <button
                    onClick={() => apply.mutate(lastRun!.run_id)}
                    disabled={apply.isPending || !!applied[lastRun.strategy_id]}
                    className="mt-2 flex items-center gap-1 rounded-lg bg-emerald-600/90 px-2.5 py-1 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
                  >
                    {apply.isPending ? <Loader2 className="size-3 animate-spin" /> : <CheckCircle2 className="size-3" />}
                    {applied[lastRun.strategy_id] ? '已应用' : '应用推荐'}
                  </button>
                </div>
              ) : (
                <div className="text-sm text-muted">本轮无合格候选 (所有候选均未全面优于基线)</div>
              )}
            </div>
            {lastRun.progress && lastRun.progress.length > 0 && (
              <div className="mt-2 border-t border-zinc-800 pt-2">
                <div className="mb-1 text-[11px] text-muted">运行日志</div>
                <div className="max-h-28 space-y-0.5 overflow-y-auto font-mono text-[11px] text-muted">
                  {lastRun.progress.map((line, i) => <div key={i}>{line}</div>)}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* 已应用参数 */}
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
        <div className="mb-3 flex items-center gap-2">
          <CheckCircle2 className="size-4 text-emerald-400" />
          <span className="text-sm font-semibold">已应用参数</span>
          <span className="text-xs text-muted">{Object.keys(applied).length} 个策略使用进化参数</span>
        </div>
        {Object.keys(applied).length === 0 ? (
          <div className="text-sm text-muted">尚无应用 — 运行进化并在推荐记录中"应用"后, 参数将并入策略扫描</div>
        ) : (
          <div className="grid gap-2 md:grid-cols-2">
            {Object.entries(applied).map(([sid, o]) => (
              <div key={sid} className="rounded-lg border border-zinc-800 bg-zinc-950/60 p-3">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium">{sid} · {o.name}</span>
                  <button
                    onClick={() => clear.mutate(sid)}
                    disabled={clear.isPending}
                    className="flex items-center gap-1 rounded-lg border border-zinc-800 px-2 py-1 text-[11px] text-muted hover:bg-zinc-900 disabled:opacity-50"
                  >
                    <Undo2 className="size-3" /> 撤销
                  </button>
                </div>
                <div className="mt-1 text-[11px] text-muted">run: {o.run_id} · 应用 {o.applied_at}</div>
                <div className="mt-1 truncate font-mono text-[11px] text-muted">
                  params: {JSON.stringify(o.params)} · exec: {JSON.stringify(o.exec_params)}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 推荐记录 */}
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
        <div className="mb-3 flex items-center gap-2">
          <Sparkles className="size-4 text-primary" />
          <span className="text-sm font-semibold">推荐记录</span>
          <span className="text-xs text-muted">{recsQ.data?.count ?? records.length} 条 (倒序)</span>
        </div>
        {records.length === 0 ? (
          <div className="flex flex-col items-center gap-2 py-6 text-center text-sm text-muted">
            <Sparkles className="size-8 opacity-30" />
            暂无进化记录 — 选一个策略运行进化
          </div>
        ) : (
          <div className="space-y-3">
            {records.map((r) => (
              <div key={r.run_id} className="rounded-lg border border-zinc-800 bg-zinc-950/60 p-3">
                <div className="mb-2 flex flex-wrap items-center gap-2">
                  <RunStatus status={r.status} />
                  <span className="text-sm font-medium">{r.strategy_id}</span>
                  <span className="text-xs text-muted">{r.created_at} · {r.run_id}</span>
                  <span className="text-xs text-muted">{r.windows.length} 验证窗口</span>
                </div>
                {r.recommendation ? (
                  <div className="flex flex-col gap-2">
                    <CandidateCard name={r.recommendation.name} score={r.recommendation.score}
                      validate={r.recommendation.validate} wf={r.recommendation.walk_forward}
                      baseline={r.baseline} applied={!!applied[r.strategy_id] && applied[r.strategy_id].run_id === r.run_id} />
                    {!(applied[r.strategy_id]?.run_id === r.run_id) && (
                      <button
                        onClick={() => apply.mutate(r.run_id)}
                        disabled={apply.isPending}
                        className="flex w-fit items-center gap-1 rounded-lg bg-emerald-600/90 px-2.5 py-1 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
                      >
                        {apply.isPending ? <Loader2 className="size-3 animate-spin" /> : <CheckCircle2 className="size-3" />}
                        应用此推荐
                      </button>
                    )}
                  </div>
                ) : (
                  <div className="text-sm text-muted">无合格候选 ({r.candidates.length} 个候选均未全面优于基线)</div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

/**
 * 驾驶舱 (Cockpit) — 一屏聚合数据链路/市场环境/主线认证/工作流状态/异常提醒。
 *
 * 目标: "一眼就能发现各种情况"。
 *  - 顶部状态灯: 全绿(ok) / 有提醒(attention), 异常自动高亮
 *  - 数据链路: 日线 → 富化 → 环境 → 主线 → 计划 → 复盘 逐层分区/最新日期, 缺口标红
 *  - 主线认证: 金牌主升/主升/脉冲/轮动 + 龙头身位股
 *  - 环境: regime 分数 + state/phase
 *  - 工作流: 最新计划/复盘/进化状态
 *  - 提醒列表: error/warn/info 分级
 *
 * 数据: GET /api/cockpit/overview
 */
import { useQuery } from '@tanstack/react-query'
import {
  Loader2, RefreshCw, CircleAlert, TriangleAlert, Info,
  Gauge, Flame, Activity, Database, TrendingUp, ShieldCheck,
} from 'lucide-react'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { cn } from '@/lib/cn'
import { PageHeader } from '@/components/PageHeader'

const LEVEL_META = {
  error: { icon: CircleAlert, cls: 'bg-red-500/10 text-red-400 border-red-500/30' },
  warn: { icon: TriangleAlert, cls: 'bg-amber-500/10 text-amber-400 border-amber-500/30' },
  info: { icon: Info, cls: 'bg-sky-500/10 text-sky-400 border-sky-500/30' },
} as const

function HealthDot({ layer }: { layer: { partitions: number; latest: string | null; label: string; key: string } }) {
  const ok = layer.partitions > 0
  const behind = layer.key === 'kline_daily_enriched' && !ok
  return (
    <div className={cn(
      'flex flex-col gap-1 rounded-xl border p-3',
      ok ? 'bg-base-200/60 border-base-300' : 'bg-red-500/5 border-red-500/30',
    )}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-medium">{layer.label}</span>
        <span className={cn('size-2.5 rounded-full', ok ? 'bg-emerald-500' : 'bg-red-500 animate-pulse')} />
      </div>
      <div className="text-xs text-muted">
        {ok ? `${layer.partitions} 分区` : '缺失'}
      </div>
      <div className="text-[11px] text-muted/80">
        {layer.latest ?? '—'}{behind ? ' · 落后日线' : ''}
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
  const overview = useQuery({ queryKey: QK.cockpitOverview, queryFn: api.cockpitOverview })
  const d = overview.data

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

  return (
    <div className="space-y-4">
      <PageHeader
        title="驾驶舱"
        desc="一屏聚合数据链路 / 市场环境 / 主线认证 / 工作流状态, 异常自动高亮"
        actions={
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => overview.refetch()}
          >
            <RefreshCw className={cn('size-4', overview.isFetching && 'animate-spin')} />
            刷新
          </button>
        }
      />

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
        <div className="rounded-xl border bg-base-200/60 p-4">
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
                <span className="rounded-full bg-base-300 px-2 py-0.5 text-xs">{d.regime.phase_label ?? d.regime.phase}</span>
                <span className="text-[11px] text-muted">{d.regime.date}</span>
              </div>
            </div>
          ) : (
            <p className="text-sm text-amber-400">{d.regime.detail ?? '环境分未生成'}</p>
          )}
        </div>

        {/* 主线认证 */}
        <div className="rounded-xl border bg-base-200/60 p-4">
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
        <div className="rounded-xl border bg-base-200/60 p-4">
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
              <div className="border-t border-base-300 pt-2 text-xs text-muted">
                最近复盘 {d.workflow.latest_review.trade_date} · 胜率{' '}
                <span className="text-bull">{d.workflow.latest_review.summary.win_rate?.toFixed?.(1) ?? '—'}%</span>
              </div>
            )}
            <div className="border-t border-base-300 pt-2 text-xs text-muted">
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
          <div className="rounded-xl border border-base-300 p-4 text-sm text-muted">暂无提醒, 一切正常</div>
        ) : (
          <div className="space-y-2">
            {d.alerts.map((a, i) => {
              const meta = LEVEL_META[a.level]
              const Icon = meta.icon
              return (
                <div key={i} className={cn('flex items-start gap-3 rounded-xl border p-3', meta.cls)}>
                  <Icon className="mt-0.5 size-4 shrink-0" />
                  <div className="min-w-0">
                    <div className="text-sm font-medium">{a.title}</div>
                    <div className="text-xs opacity-80">{a.detail}</div>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}

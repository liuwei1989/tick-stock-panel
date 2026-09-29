/**
 * 主线认证页 — 题材持续性认证 / 主线龙头身位。
 *
 * 认证规则 (对照自在量化"真·主线认证"产品化):
 *  - gold 金牌主升: 连续≥3日居前 且 5日均强度≥60
 *  - up   主升:     连续≥2日居前
 *  - pulse 日内脉冲: 仅当日强 (score≥50)
 *  - rotate 轮动:    其余
 *  - 龙头身位股: 当日 rank1 题材的 leader_symbol
 *
 * 数据: cockpit.mainline_certification (近 8 日主线时序)
 */
import { useQuery } from '@tanstack/react-query'
import { RefreshCw, Flame, Crown, Loader2, AlertTriangle } from 'lucide-react'
import { api } from '@/lib/api'
import { cn } from '@/lib/cn'
import { PageHeader } from '@/components/PageHeader'
import { Button } from '@/components/ui/Button'

const LEVEL_META: Record<string, { label: string; cls: string; hint: string }> = {
  gold: { label: '金牌主升', cls: 'bg-yellow-500/15 text-yellow-400 border-yellow-500/40', hint: '连续上榜 + 5日均强度≥60' },
  up: { label: '主升', cls: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/40', hint: '连续上榜 ≥2 日' },
  pulse: { label: '日内脉冲', cls: 'bg-orange-500/15 text-orange-400 border-orange-500/40', hint: '仅当日强, 防追高' },
  rotate: { label: '轮动', cls: 'bg-slate-500/15 text-slate-400 border-slate-500/40', hint: '持续性不足' },
}

export default function Mainline() {
  const q = useQuery({ queryKey: ['mainline', 'cert'], queryFn: api.cockpitOverview })
  const d = q.data

  return (
    <div className="space-y-4 pb-10">
      <PageHeader
        title="主线认证"
        subtitle="题材持续性认证 + 主线龙头身位"
        right={
          <Button variant="ghost" size="sm" onClick={() => q.refetch()}>
            <RefreshCw className={cn('size-4', q.isFetching && 'animate-spin')} /> 刷新
          </Button>
        }
      />

      {q.isLoading && <div className="grid min-h-[40vh] place-items-center"><Loader2 className="size-6 animate-spin text-muted" /></div>}
      {!q.isLoading && d && !d.mainline.available && (
        <div className="mx-5 flex items-center gap-2 rounded-xl border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-400">
          <AlertTriangle className="size-4" /> {d.mainline.detail ?? '主线时序未生成'}
        </div>
      )}

      {d?.mainline.available && (
        <div className="mx-5 space-y-4">
          {/* 龙头身位 */}
          {d.mainline.leaders && d.mainline.leaders.length > 0 && (
            <div className="flex flex-wrap items-center gap-3 rounded-xl border border-yellow-500/30 bg-gradient-to-r from-yellow-500/10 to-transparent p-4">
              <Crown className="size-5 text-yellow-400" />
              <div className="text-sm">
                <span className="text-muted">主线龙头 · </span>
                <span className="font-semibold">{d.mainline.leaders[0].member}</span>
                <span className="ml-2 font-mono text-yellow-400">{d.mainline.leaders[0].symbol}</span>
                <span className="ml-2 text-xs text-muted">
                  {d.mainline.leaders[0].max_boards} 板 · {d.mainline.leaders[0].streak_days} 日居前 · 强度 {d.mainline.leaders[0].score.toFixed(0)}
                </span>
              </div>
              <div className="ml-auto rounded-full bg-base/60 px-2 py-0.5 text-[11px] text-muted">
                金牌主线 {d.mainline.gold_count} · 截至 {d.mainline.as_of}
              </div>
            </div>
          )}

          {/* 认证说明 */}
          <div className="flex flex-wrap gap-2">
            {Object.entries(LEVEL_META).map(([k, v]) => (
              <span key={k} className={cn('inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px]', v.cls)}>
                {v.label} · {v.hint}
              </span>
            ))}
          </div>

          {/* 题材表 */}
          <div className="overflow-hidden rounded-card border border-border bg-surface">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border bg-base/40 text-left text-[11px] uppercase tracking-wider text-muted">
                  <th className="px-3 py-2">#</th>
                  <th className="px-3 py-2">题材</th>
                  <th className="px-3 py-2">认证</th>
                  <th className="px-3 py-2 text-right">强度</th>
                  <th className="px-3 py-2 text-right">5日均</th>
                  <th className="px-3 py-2 text-right">居前</th>
                  <th className="px-3 py-2 text-right">涨停</th>
                  <th className="px-3 py-2 text-right">最高板</th>
                  <th className="px-3 py-2">身位股</th>
                </tr>
              </thead>
              <tbody>
                {d.mainline.items!.map((i, idx) => {
                  const m = LEVEL_META[i.level]
                  return (
                    <tr key={i.member} className="border-b border-border/60 last:border-0 hover:bg-base/40">
                      <td className="px-3 py-2 text-xs text-muted">{idx + 1}</td>
                      <td className="px-3 py-2 font-medium">{i.member}</td>
                      <td className="px-3 py-2">
                        <span className={cn('inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px]', m.cls)}>
                          {i.level === 'gold' && <Flame className="size-3" />}
                          {m.label}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-right font-mono">{i.score.toFixed(0)}</td>
                      <td className="px-3 py-2 text-right font-mono text-muted">{i.avg5_score.toFixed(1)}</td>
                      <td className="px-3 py-2 text-right font-mono">{i.streak_days}日</td>
                      <td className="px-3 py-2 text-right font-mono">{i.limit_up_count}</td>
                      <td className="px-3 py-2 text-right font-mono">{i.max_boards}</td>
                      <td className="px-3 py-2 font-mono">{i.leader_symbol ?? '—'}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

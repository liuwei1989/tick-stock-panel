/**
 * 竞价定盘页 — 盘前竞价风向标。
 *
 * 数据: GET /api/market-recap/auction-benchmark
 *  - 每日 5~6 只竞价异动股 (同花顺筛选), 附概念标签
 *  - 当日开盘买→收盘卖均值 +0.54% (服务端回测), 高开≥5% 子集为追高陷阱
 *  - 次日无显著优势 → 定位为"当日观察名单"而非隔夜轮动信号
 * fuyao 数据源未配置时降级提示。
 */
import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { RefreshCw, Gavel, Loader2, AlertTriangle, TrendingUp, TrendingDown, Scale } from 'lucide-react'
import { api, type AuctionBenchmarkPayload } from '@/lib/api'
import { cn } from '@/lib/cn'
import { PageHeader } from '@/components/PageHeader'

function pctCls(v: number | null | undefined): string {
  if (v == null || v === 0) return 'text-muted'
  return v > 0 ? 'text-bull' : 'text-bear'
}

export default function Auction() {
  const [date, setDate] = useState('')
  const q = useQuery({ queryKey: ['auction-page', date || 'latest'], queryFn: () => api.auctionBenchmark(date || undefined) })
  const d = q.data as AuctionBenchmarkPayload | undefined

  const stats = useMemo(() => {
    const items = d?.items ?? []
    const withDay0 = items.filter(i => i.day0_oc != null)
    const avgDay0 = withDay0.length ? withDay0.reduce((a, i) => a + (i.day0_oc ?? 0), 0) / withDay0.length : null
    const withD1 = items.filter(i => i.d1_pct != null)
    const avgD1 = withD1.length ? withD1.reduce((a, i) => a + (i.d1_pct ?? 0), 0) / withD1.length : null
    const highOpen = items.filter(i => (i.auction_pct ?? 0) >= 5)
    return { count: items.length, avgDay0, avgD1, highOpenCount: highOpen.length }
  }, [d])

  return (
    <div className="space-y-4 pb-10">
      <PageHeader
        title="竞价定盘"
        subtitle="盘前竞价风向标 · 当日观察名单 (非隔夜信号)"
        right={
          <div className="flex items-center gap-2">
            <input type="date" value={date} onChange={e => setDate(e.target.value)} className="input input-sm input-bordered" />
            <button className="btn btn-ghost btn-sm" onClick={() => q.refetch()}>
              <RefreshCw className={cn('size-4', q.isFetching && 'animate-spin')} /> 刷新
            </button>
          </div>
        }
      />

      {d?.state === 'source_unavailable' && (
        <div className="mx-5 flex items-center gap-2 rounded-xl border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-400">
          <AlertTriangle className="size-4" /> 竞价定盘需要 fuyao 数据源 (同花顺竞价筛选), 当前未配置
        </div>
      )}
      {d?.state === 'no_data' && (
        <div className="mx-5 p-3 text-sm text-muted">{d.message ?? '竞价数据拉取失败'}</div>
      )}

      {d && d.state !== 'source_unavailable' && d.state !== 'no_data' && (
        <div className="mx-5 space-y-4">
          {/* 统计条 */}
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <div className="rounded-xl border bg-base-200/60 p-3">
              <div className="flex items-center gap-1.5 text-[11px] text-muted"><Gavel className="size-3" /> 名单</div>
              <div className="mt-1 text-xl font-semibold">{stats.count} 只</div>
            </div>
            <div className="rounded-xl border bg-base-200/60 p-3">
              <div className="flex items-center gap-1.5 text-[11px] text-muted"><TrendingUp className="size-3" /> 当日均值 (开→收)</div>
              <div className={cn('mt-1 text-xl font-semibold', pctCls(stats.avgDay0))}>
                {stats.avgDay0 == null ? '—' : `${(stats.avgDay0 * 100).toFixed(2)}%`}
              </div>
            </div>
            <div className="rounded-xl border bg-base-200/60 p-3">
              <div className="flex items-center gap-1.5 text-[11px] text-muted"><TrendingDown className="size-3" /> 次日均值</div>
              <div className={cn('mt-1 text-xl font-semibold', pctCls(stats.avgD1))}>
                {stats.avgD1 == null ? '—' : `${(stats.avgD1 * 100).toFixed(2)}%`}
              </div>
            </div>
            <div className="rounded-xl border bg-base-200/60 p-3">
              <div className="flex items-center gap-1.5 text-[11px] text-muted"><Scale className="size-3" /> 高开 ≥5% (追高风险)</div>
              <div className="mt-1 text-xl font-semibold text-orange-400">{stats.highOpenCount} 只</div>
            </div>
          </div>

          <div className="rounded-xl border bg-base-200/60 p-4">
            <div className="mb-2 flex items-center gap-2 text-sm font-semibold">
              <Gavel className="size-4" /> 竞价名单 {d.trade_date && <span className="text-xs font-normal text-muted">({d.trade_date})</span>}
              {d.state === 'fallback_prev' && <span className="rounded-full bg-amber-500/10 px-2 py-0.5 text-[11px] text-amber-400">当日未发布, 回退上一期</span>}
            </div>
            {stats.count === 0 ? <p className="text-sm text-muted">本期无竞价名单</p> : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-base-300 bg-base/40 text-left text-[11px] uppercase tracking-wider text-muted">
                      <th className="px-3 py-2">股票</th>
                      <th className="px-3 py-2 text-right">竞价涨跌</th>
                      <th className="px-3 py-2">概念</th>
                      <th className="px-3 py-2 text-right">当日开→收</th>
                      <th className="px-3 py-2 text-right">当日全天</th>
                      <th className="px-3 py-2 text-right">次日</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(d.items ?? []).map(r => (
                      <tr key={r.thscode} className="border-b border-base-300/60 last:border-0 hover:bg-base/40">
                        <td className="px-3 py-2 font-medium">{r.name ?? r.thscode}</td>
                        <td className={cn('px-3 py-2 text-right font-mono', (r.auction_pct ?? 0) >= 5 ? 'text-orange-400' : pctCls(r.auction_pct))}>
                          {r.auction_pct != null ? `${r.auction_pct > 0 ? '+' : ''}${r.auction_pct.toFixed(2)}%` : '—'}
                          {(r.auction_pct ?? 0) >= 5 && <span className="ml-1 text-[10px]">⚠追高</span>}
                        </td>
                        <td className="px-3 py-2">
                          <div className="flex max-w-[260px] flex-wrap gap-1">
                            {(r.tags ?? []).slice(0, 4).map(t => (
                              <span key={t} className="rounded bg-base-300 px-1 py-0.5 text-[10px] text-muted">{t}</span>
                            ))}
                          </div>
                        </td>
                        <td className={cn('px-3 py-2 text-right font-mono', pctCls(r.day0_oc))}>
                          {r.day0_oc != null ? `${(r.day0_oc * 100).toFixed(2)}%` : '—'}
                        </td>
                        <td className={cn('px-3 py-2 text-right font-mono', pctCls(r.day0_pct))}>
                          {r.day0_pct != null ? `${(r.day0_pct * 100).toFixed(2)}%` : '—'}
                        </td>
                        <td className={cn('px-3 py-2 text-right font-mono', pctCls(r.d1_pct))}>
                          {r.d1_pct != null ? `${(r.d1_pct * 100).toFixed(2)}%` : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <p className="mt-2 text-[11px] text-muted">
              口径: 名单当日均值 +0.54% vs 全市场 +0.10%; 高开 ≥5% 子集当日 -1.97% (追高陷阱); 次日无显著优势, 定位当日观察名单。
            </p>
          </div>
        </div>
      )}

      {q.isLoading && <div className="grid min-h-[30vh] place-items-center"><Loader2 className="size-6 animate-spin text-muted" /></div>}
    </div>
  )
}

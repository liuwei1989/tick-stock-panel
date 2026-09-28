/**
 * 龙虎榜游资跟踪页 — 游资席位动向 / 净买个股 / 机构对比。
 *
 * 数据: GET /api/market-recap/dragon-tiger (all / org / hot_money 三榜)
 *  - hot_money: 游资席位榜 (席位名 + 净买 + 关联股票)
 *  - all: 当日龙虎榜个股 (净买额排序)
 *  - org: 机构席位视角
 * fuyao 数据源未配置时降级提示。
 */
import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { RefreshCw, Trophy, Users, Landmark, Loader2, AlertTriangle } from 'lucide-react'
import { api, type DragonTigerPayload } from '@/lib/api'
import { cn } from '@/lib/cn'
import { PageHeader } from '@/components/PageHeader'

function fmtMoney(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—'
  const abs = Math.abs(v)
  const unit = abs >= 1e8 ? '亿' : abs >= 1e4 ? '万' : ''
  const n = abs >= 1e8 ? v / 1e8 : abs >= 1e4 ? v / 1e4 : v
  return `${v >= 0 ? '+' : '-'}${Math.abs(n).toFixed(abs >= 1e8 ? 2 : 0)}${unit}`
}

function priceCls(v: number | null | undefined): string {
  if (v == null || v === 0) return 'text-muted'
  return v > 0 ? 'text-bull' : 'text-bear'
}

export default function DragonTiger() {
  const [date, setDate] = useState('')
  const [tab, setTab] = useState<'hot_money' | 'all' | 'org'>('hot_money')
  const q = useQuery({ queryKey: ['dragon-tiger-page', date || 'latest'], queryFn: () => api.dragonTiger(date || undefined) })
  const d = q.data as DragonTigerPayload | undefined

  const hotMoney = useMemo(() => {
    const items = d?.hot_money?.hot_money_items ?? []
    return [...items].sort((a, b) => (b.buying ?? -Infinity) - (a.buying ?? -Infinity)).slice(0, 12)
  }, [d])
  const stocks = useMemo(() => {
    const key = tab === 'org' ? 'org_net_value' : 'net_value'
    const items = tab === 'org' ? d?.org?.stock_items : d?.all?.stock_items
    return [...(items ?? [])].sort((a, b) => (b[key] ?? -Infinity) - (a[key] ?? -Infinity)).slice(0, 15)
  }, [d, tab])

  return (
    <div className="space-y-4 pb-10">
      <PageHeader
        title="龙虎榜 · 游资"
        subtitle="游资席位动向 / 净买个股 / 机构对比"
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
          <AlertTriangle className="size-4" /> 龙虎榜需要 fuyao 数据源 (同花顺特色数据), 当前未配置
        </div>
      )}
      {d?.state === 'no_data' && (
        <div className="mx-5 p-3 text-sm text-muted">{d.message ?? '龙虎榜数据拉取失败'}</div>
      )}

      {d && d.state !== 'source_unavailable' && d.state !== 'no_data' && (
        <div className="mx-5 space-y-4">
          <div className="flex flex-wrap items-center gap-3 rounded-xl border bg-base-200/60 p-3 text-xs text-muted">
            <span>交易日 <span className="font-mono text-foreground">{d.trade_date ?? '—'}</span></span>
            {d.state === 'fallback_prev' && <span className="rounded-full bg-amber-500/10 px-2 py-0.5 text-amber-400">当日未发布, 已回退上一期</span>}
            <div className="ml-auto flex gap-1">
              {([['hot_money', '游资席位', Users], ['all', '全部', Trophy], ['org', '机构', Landmark]] as const).map(([k, label, Icon]) => (
                <button key={k} onClick={() => setTab(k)}
                  className={cn('inline-flex items-center gap-1 rounded-lg px-2.5 py-1 text-xs transition-colors',
                    tab === k ? 'bg-primary text-primary-content' : 'hover:bg-base-300')}>
                  <Icon className="size-3.5" /> {label}
                </button>
              ))}
            </div>
          </div>

          {/* 游资席位 Top */}
          {tab === 'hot_money' && (
            <div className="grid gap-4 lg:grid-cols-2">
              <div className="rounded-xl border bg-base-200/60 p-4">
                <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
                  <Users className="size-4" /> 游资席位净买 Top
                </div>
                {hotMoney.length === 0 ? <p className="text-sm text-muted">本期无游资席位数据</p> : (
                  <div className="space-y-1.5">
                    {hotMoney.map((h, i) => (
                      <div key={h.name ?? i} className="flex items-center justify-between gap-2 text-sm">
                        <div className="flex min-w-0 items-center gap-2">
                          <span className={cn('grid h-5 w-5 shrink-0 place-items-center rounded-full font-mono text-[10px]',
                            i === 0 ? 'bg-amber-500/20 text-amber-400' : 'bg-elevated text-muted')}>{i + 1}</span>
                          <span className="truncate">{h.name ?? '—'}</span>
                        </div>
                        <span className={cn('shrink-0 font-mono text-xs', priceCls(h.buying))}>{fmtMoney(h.buying)}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
              <div className="rounded-xl border bg-base-200/60 p-4">
                <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
                  <Trophy className="size-4" /> 游资关联个股
                </div>
                {hotMoney.length === 0 ? <p className="text-sm text-muted">无</p> : (
                  <div className="space-y-1.5">
                    {hotMoney.slice(0, 5).flatMap(h => (h.rows ?? []).slice(0, 2)).map((r, i) => (
                      <div key={`${r.thscode}-${i}`} className="flex items-center justify-between gap-2 text-sm">
                        <span className="min-w-0 truncate">{r.name ?? r.thscode}</span>
                        <span className={cn('shrink-0 font-mono text-xs', priceCls(r.hot_money_item_net_value))}>
                          {fmtMoney(r.hot_money_item_net_value)}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* 个股榜 */}
          {tab !== 'hot_money' && (
            <div className="overflow-hidden rounded-xl border bg-base-200/60">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-base-300 bg-base/40 text-left text-[11px] uppercase tracking-wider text-muted">
                    <th className="px-3 py-2">#</th>
                    <th className="px-3 py-2">股票</th>
                    <th className="px-3 py-2 text-right">涨跌幅</th>
                    <th className="px-3 py-2 text-right">净买额</th>
                    {tab === 'org' && <th className="px-3 py-2 text-right">机构净买</th>}
                    <th className="px-3 py-2 text-right">买入</th>
                    <th className="px-3 py-2 text-right">卖出</th>
                  </tr>
                </thead>
                <tbody>
                  {stocks.map((r, i) => (
                    <tr key={r.thscode} className="border-b border-base-300/60 last:border-0 hover:bg-base/40">
                      <td className="px-3 py-2 text-xs text-muted">{i + 1}</td>
                      <td className="px-3 py-2 font-medium">
                        {r.name ?? r.thscode}
                        {r.thscode !== (r.name ? '' : '') && r.name && <span className="ml-1 font-mono text-[10px] text-muted">{r.thscode}</span>}
                      </td>
                      <td className={cn('px-3 py-2 text-right font-mono', priceCls(r.change))}>{r.change != null ? `${(r.change * 100).toFixed(2)}%` : '—'}</td>
                      <td className={cn('px-3 py-2 text-right font-mono', priceCls(r.net_value))}>{fmtMoney(r.net_value)}</td>
                      {tab === 'org' && <td className={cn('px-3 py-2 text-right font-mono', priceCls(r.org_net_value))}>{fmtMoney(r.org_net_value)}</td>}
                      <td className="px-3 py-2 text-right font-mono text-muted">{fmtMoney(r.buy_value)}</td>
                      <td className="px-3 py-2 text-right font-mono text-muted">{fmtMoney(r.sell_value)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {q.isLoading && <div className="grid min-h-[30vh] place-items-center"><Loader2 className="size-6 animate-spin text-muted" /></div>}
    </div>
  )
}

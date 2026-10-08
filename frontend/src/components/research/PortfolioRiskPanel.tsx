import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'

const percent = (value: number | null) => value === null ? '不可计算' : `${(value * 100).toFixed(1)}%`
export function PortfolioRiskPanel() {
  const [open, setOpen] = useState(false)
  const query = useQuery({ queryKey: QK.researchPortfolioRisk, queryFn: api.researchPortfolioRisk, enabled: open, staleTime: 60_000 })
  return <details className="mt-3 border-t border-border pt-3 text-xs" onToggle={e => setOpen(e.currentTarget.open)}>
    <summary className="cursor-pointer text-secondary">模拟盘组合风险</summary>
    <p className="mt-2 text-muted">使用未复权日线收盘价核对持仓成本。缺少行情时保留缺口；净值回撤来自历史定版记录。</p>
    {query.isLoading ? <p className="mt-2">正在核验持仓…</p> : query.isError ? <p role="alert" className="mt-2 text-danger">读取失败 <button onClick={() => query.refetch()}>重试</button></p>
      : !query.data?.accounts.length ? <p className="mt-2 text-muted">尚无模拟盘账户</p>
      : query.data.accounts.map(account => <div key={account.account_id} className="mt-3 rounded border border-border p-3">
        <div className="flex flex-wrap justify-between gap-2"><span>{account.name}</span><span className="text-muted">数据日期 {account.as_of ?? '缺失'}</span></div>
        <p className="mt-2">持仓占比 {percent(account.exposure_ratio)} · 定版净值回撤 {percent(account.drawdown_ratio)}</p>
        {!account.available && <p className="mt-1 text-warning">账户或持仓行情不完整，暂不计算总资产与仓位。{account.missing_prices.join('、')}</p>}
        {account.alerts.length ? <ul className="mt-2 space-y-1 text-warning">{account.alerts.map((alert, i) => <li key={i}>{alert.message}</li>)}</ul> : <p className="mt-2 text-muted">当前可核验项目没有触发阈值</p>}
      </div>)}
  </details>
}

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { Dialog } from '@/components/ui/Dialog'
import { MarkdownRenderer } from '@/components/financials/MarkdownRenderer'

const categories: Record<string, string> = { trend: '趋势', pattern: '形态', reversal: '反转', framework: '框架' }
const regimes: Record<string, string> = { trending_up: '上行趋势', trending_down: '下行趋势', sideways: '横盘震荡', volatile: '高波动', sector_hot: '板块热点' }
const inputs: Record<string, string> = {
  get_daily_history: '历史日线', analyze_trend: '技术指标与趋势', get_realtime_quote: '实时行情',
  get_sector_rankings: '板块排名', search_stock_news: '新闻检索', get_stock_info: '公司资料',
}

export function ResearchSkillPicker({ value, onChange, disabled = false, label = '研究 Skill' }: {
  value: string[]
  onChange: (value: string[]) => void
  disabled?: boolean
  label?: string
}) {
  const catalog = useQuery({ queryKey: QK.researchSkills, queryFn: api.researchSkills, staleTime: 60_000 })
  const [preview, setPreview] = useState<string | null>(null)
  const detail = useQuery({ queryKey: QK.researchSkillDetail(preview ?? ''), queryFn: () => api.researchSkillDetail(preview!), enabled: preview !== null, staleTime: 60_000 })
  const skills = catalog.data?.skills ?? []
  const automatic = value.length === 1 && value[0] === 'auto'
  const selected = automatic ? [] : value
  const unavailable = catalog.data ? selected.filter(id => !skills.some(s => s.name === id && s.user_invocable)) : []
  function toggle(id: string) {
    if (disabled) return
    if (selected.includes(id)) onChange(selected.filter(s => s !== id))
    else if (selected.length < 3) onChange([...selected, id])
  }
  return <div role="group" aria-label={label} className="min-w-0 rounded border border-border/60 p-3 text-xs">
    <div className="flex flex-wrap items-center gap-3">
      <span className="font-medium">{label}</span>
      <button type="button" aria-pressed={automatic} disabled={disabled || !catalog.data} onClick={() => onChange(['auto'])}
        className={`disabled:opacity-50 ${automatic ? 'text-accent' : 'text-muted'}`}>自动选择</button>
      <button type="button" aria-pressed={value.length === 0} disabled={disabled} onClick={() => onChange([])}
        className={`disabled:opacity-50 ${value.length === 0 ? 'text-accent' : 'text-muted'}`}>不附加</button>
      <span className="text-muted">{automatic ? '按分析诉求匹配，未匹配时使用默认框架' : `已选 ${selected.length}/3`}</span>
    </div>
    {selected.length > 0 && <p className="mt-2 break-words text-secondary">{selected.map(id => skills.find(s => s.name === id)?.display_name ?? id).join(' · ')}</p>}
    {unavailable.length > 0 && <p role="alert" className="mt-2 text-warning">部分已选 Skill 不再可用，请重新选择：{unavailable.join('、')}</p>}
    {catalog.isLoading ? <p className="mt-2 text-muted">正在加载内置 Skill…</p>
      : catalog.isError ? <p role="alert" className="mt-2 text-danger">Skill 目录加载失败 <button type="button" onClick={() => catalog.refetch()}>重试</button></p>
      : <details className="mt-2">
        <summary className="cursor-pointer text-accent">浏览 Skill · 内置 {catalog.data?.builtin_count ?? 0} 个{catalog.data?.custom_count ? ` · 自定义 ${catalog.data.custom_count} 个` : ''}</summary>
        {catalog.data?.errors.map(error => <p key={error} role="alert" className="mt-2 text-warning">{error}</p>)}
        {!skills.length && <p className="mt-2 text-muted">暂无可用 Skill</p>}
        <div className="mt-3 grid gap-2 md:grid-cols-2 xl:grid-cols-3">
          {skills.map(skill => <div key={skill.name} className={`min-w-0 rounded border p-3 ${selected.includes(skill.name) ? 'border-accent/60 bg-accent/5' : 'border-border'}`}>
            <label className="flex items-center gap-2 font-medium">
              <input type="checkbox" checked={selected.includes(skill.name)} aria-label={`选择 ${skill.display_name}`}
                disabled={disabled || !skill.user_invocable || (!selected.includes(skill.name) && selected.length >= 3)} onChange={() => toggle(skill.name)} />
              {skill.display_name}
            </label>
            <p className="mt-1 text-[10px] text-muted">{categories[skill.category] ?? skill.category} · {skill.source === 'builtin' ? 'DSA 内置' : '自定义'}{skill.default_active ? ' · 默认框架' : ''}{!skill.user_invocable ? ' · 不可手动调用' : ''}</p>
            <p className="mt-2 line-clamp-2 leading-relaxed text-secondary">{skill.description}</p>
            <button type="button" className="mt-2 text-accent" onClick={() => setPreview(skill.name)} aria-label={`查看 ${skill.display_name} 的定义`}>查看定义</button>
          </div>)}
        </div>
      </details>}
    <Dialog open={preview !== null} onClose={() => setPreview(null)} title={skills.find(s => s.name === preview)?.display_name ?? '研究 Skill'} size="xl">
      {detail.isLoading ? <p className="text-xs text-muted">正在读取定义…</p>
        : detail.isError ? <p role="alert" className="text-xs text-danger">定义读取失败 <button type="button" onClick={() => detail.refetch()}>重试</button></p>
        : detail.data && <div className="space-y-4">
          <p className="text-xs text-secondary">{detail.data.description}</p>
          {detail.data.market_regimes.length > 0 && <p className="text-xs text-muted">适用行情：{detail.data.market_regimes.map(r => regimes[r] ?? r).join('、')}</p>}
          {detail.data.aliases.length > 0 && <p className="text-xs text-muted">别名：{detail.data.aliases.join('、')}</p>}
          {detail.data.required_tools.length > 0 && <p className="text-xs text-muted">所需资料：{detail.data.required_tools.map(t => inputs[t] ?? t).join('、')}。执行时以实际提供的数据为准，缺失资料会保留为待核验项。</p>}
          <MarkdownRenderer content={detail.data.instructions} />
        </div>}
    </Dialog>
  </div>
}

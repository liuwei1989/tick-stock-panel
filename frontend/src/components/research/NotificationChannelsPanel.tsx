import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import type { NotificationChannel } from '@/lib/researchTypes'

const labels: Record<string,string> = { url:'通知地址', token:'访问令牌', secret:'签名密钥', chat_id:'会话 ID', user:'用户 Key', sendkey:'SendKey' }
export function NotificationChannelsPanel() {
  const channels = useQuery({ queryKey: QK.notificationChannels, queryFn: api.notificationChannels })
  const history = useQuery({ queryKey: QK.notificationDeliveries, queryFn: api.notificationDeliveries, refetchInterval: 30_000 })
  return <section className="rounded-card border border-border bg-surface/40 p-4">
    <h3 className="text-sm font-semibold">更多通知渠道</h3>
    <p className="mt-1 text-xs text-muted">保存后，在监控规则中勾选对应渠道。凭据只写入本地密钥存储，留空保留原值。</p>
    {channels.isLoading ? <p className="text-xs">加载中…</p> : channels.isError ? <p role="alert" className="text-xs text-danger">渠道加载失败 <button onClick={() => channels.refetch()}>重试</button></p>
      : <div className="mt-3 grid gap-2 sm:grid-cols-2">{channels.data?.channels.map(c => <ChannelForm key={c.id} channel={c} />)}</div>}
    <details className="mt-3 text-xs"><summary>最近投递结果</summary>
      {history.isError ? <p>投递记录加载失败</p> : history.data?.deliveries.slice(0,20).map(d => <p key={d.id} className="mt-1 text-muted">{d.created_at} · {d.channel} · {d.status === 'sent' ? '已发送' : '失败'} · 尝试 {d.attempts} 次</p>)}
      {history.data?.deliveries.length === 0 && <p className="mt-2 text-muted">尚无投递记录</p>}
    </details>
  </section>
}
function ChannelForm({ channel }: { channel: NotificationChannel }) {
  const qc = useQueryClient()
  const [values,setValues] = useState<Record<string,string>>({})
  const [busy,setBusy] = useState(false)
  const [message,setMessage] = useState('')
  async function save(clear = false) {
    setBusy(true); setMessage('')
    try {
      await api.notificationChannelSave(channel.id, clear ? Object.fromEntries(channel.fields.map(f => [f,''])) : Object.fromEntries(Object.entries(values).filter(([,v]) => v)))
      setValues({}); setMessage(clear ? '已清除配置' : '已保存')
      await qc.invalidateQueries({ queryKey: QK.notificationChannels })
    } catch (e) { setMessage(e instanceof Error ? e.message : '保存失败') }
    finally { setBusy(false) }
  }
  return <details className="rounded border border-border p-3 text-xs">
    <summary className="cursor-pointer">{channel.label} · {channel.configured ? '已配置' : '未配置'}</summary>
    <form className="mt-3 space-y-2" onSubmit={e => { e.preventDefault(); save() }}>
      {channel.fields.map(f => <label className="block text-secondary" key={f}>{labels[f] ?? f}{channel.required_fields.includes(f) ? ' *' : ''}
        <input type="password" autoComplete="new-password" value={values[f] ?? ''} onChange={e => setValues(v => ({...v,[f]:e.target.value}))}
          placeholder={channel.set_fields.includes(f) ? '已设置，留空保留' : '未设置'} className="mt-1 w-full rounded border border-border bg-surface px-2 py-1" />
      </label>)}
      {message && <p role="status">{message}</p>}
      <div className="flex gap-3"><button type="submit" disabled={busy} className="text-accent">保存</button>
        <button type="button" disabled={busy || !channel.set_fields.length} onClick={() => save(true)} className="text-muted">清除配置</button></div>
    </form>
  </details>
}

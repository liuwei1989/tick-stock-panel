import type { ResearchArtifact } from '@/lib/researchTypes'

const direction = { bullish: '偏强', bearish: '偏弱', neutral: '中性', unknown: '未判定' }
const quality = { available: '有数据', missing: '缺失', unknown: '时效待核验', not_applicable: '不适用' }
export function ResearchArtifactView({ artifact }: { artifact: ResearchArtifact }) {
  return <div className="my-3 space-y-3 rounded-card border border-border bg-surface/60 p-3 text-xs">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <strong>结构化研究 · {direction[artifact.thesis.direction]}</strong>
      <span className="text-secondary">研究评分 {artifact.thesis.score ?? '—'} · 输入完整度 {artifact.data_quality.completeness_score}%</span>
    </div>
    {artifact.thesis.summary && <p className="break-words text-secondary">{artifact.thesis.summary}</p>}
    <div className="flex flex-wrap gap-2">
      {artifact.evidence.map(e => <span key={e.id} className="rounded border border-border px-2 py-1 text-muted">
        {e.label}：{quality[e.status]}{e.as_of ? ` · ${e.as_of}` : ''}
      </span>)}
    </div>
    {artifact.parse_status !== 'validated' && <p className="text-amber-500">模型未返回合格的结构化结果，保留正文供人工核验。</p>}
    <div className="grid gap-3 sm:grid-cols-2">
      {[
        ['失效条件', artifact.invalidation_conditions], ['核验清单', artifact.next_actions],
        ['风险', artifact.thesis.risks], ['催化线索', artifact.thesis.catalysts],
        ['研究分歧', artifact.thesis.disagreement],
      ].map(([title, values]) => (values as string[]).length > 0 && <div key={title as string}>
        <div className="mb-1 font-medium">{title}</div>
        <ul className="list-inside list-disc space-y-1 break-words text-secondary">
          {(values as string[]).map((v, i) => <li key={i}>{v}</li>)}
        </ul>
      </div>)}
    </div>
  </div>
}

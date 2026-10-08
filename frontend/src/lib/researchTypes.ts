export interface ResearchDecision {
  summary: string
  score: number | null
  direction: 'bullish' | 'bearish' | 'neutral' | 'unknown'
  evidence_ids: string[]
  risks: string[]
  catalysts: string[]
  invalidation: string[]
  checklist: string[]
  disagreement: string[]
}
export interface ResearchArtifact {
  schema_version: 1
  artifact_id: string
  symbol: string
  asset_type: string
  created_at: string
  thesis: ResearchDecision
  evidence: { id: string; label: string; status: 'available' | 'missing' | 'unknown' | 'not_applicable'; as_of: string | null; count: number }[]
  invalidation_conditions: string[]
  next_actions: string[]
  data_quality: { completeness_score: number; level: 'usable' | 'limited'; limitations: string[] }
  parse_status: 'validated' | 'unstructured' | 'invalid'
}
export interface ResearchSignal {
  id: string
  symbol: string
  asset_type: string
  report_id: string
  artifact: ResearchArtifact
  signal_date: string
  status: 'watching' | 'review_required' | 'dismissed' | 'evaluated'
  version: number
  history: { from: string; to: string; reason: string; at: string }[]
  outcome?: ResearchOutcome
}
export interface ResearchOutcome {
  status: 'pending' | 'observed'
  return_ratio: number | null
  observations: number
  basis: string
  description?: string
}
export interface ResearchRun {
  id: string
  symbol: string
  mode: string
  status: 'running' | 'succeeded' | 'degraded' | 'failed' | 'cancelled' | 'interrupted'
  created_at: string
  report_id?: string
  trajectory: { stage: string; label: string; status: string; duration_ms?: number; failure_code?: string }[]
}
export interface ResearchSkill {
  name: string
  display_name: string
  description: string
  category: string
  source: 'builtin' | 'custom'
  aliases: string[]
  core_rules: number[]
  required_tools: string[]
  allowed_tools: string[]
  default_active: boolean
  default_router: boolean
  default_priority: number
  market_regimes: string[]
  user_invocable: boolean
  disable_model_invocation: boolean
}
export interface ResearchSkillDetail extends ResearchSkill { instructions: string }
export interface ResearchBatch {
  id: string
  status: string
  symbols: string[]
  items: { symbol: string; status: string; run_id?: string }[]
  created_at: string
}
export interface NotificationChannel {
  id: string
  label: string
  fields: string[]
  required_fields: string[]
  configured: boolean
  set_fields: string[]
}

export interface ResearchSchedule {
  enabled: boolean
  symbols: string[]
  hour: number
  minute: number
  mode: 'quick' | 'standard' | 'full'
  skill_ids: string[]
}
export interface PortfolioRiskAccount {
  account_id: string
  name: string
  as_of: string | null
  available: boolean
  missing_prices: string[]
  equity: number | null
  exposure_ratio: number | null
  drawdown_ratio: number | null
  alerts: { kind: string; symbol?: string; message: string }[]
}

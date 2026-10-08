import { beforeEach, expect, test, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  stream: vi.fn(),
  save: vi.fn(),
  list: vi.fn(),
}))
vi.mock('./api', () => ({
  api: { stockAnalyzeStream: mocks.stream, stockAnalysisReportSave: mocks.save, stockAnalysisReportsList: mocks.list },
  friendlyStreamError: (message: string) => message,
}))
vi.mock('react', () => ({ useSyncExternalStore: (_subscribe: unknown, snapshot: () => unknown) => snapshot() }))

beforeEach(() => {
  vi.resetModules()
  vi.clearAllMocks()
  mocks.list.mockResolvedValue({ reports: [] })
})

test('interrupted stream retains text but releases the active slot as an error', async () => {
  mocks.stream.mockImplementation(async function* () {
    yield { type: 'run', run_id: 'run1' }
    yield { type: 'delta', content: '部分研究' }
  })
  const store = await import('./stockAnalysisStore')
  await store.startAnalysis('000001.SZ', '平安银行')
  await vi.waitFor(() => expect(store.useDialogTask().task).toMatchObject({ phase: 'error', content: '部分研究' }))
  expect(mocks.save).not.toHaveBeenCalled()
})

test('backend archived report is retained without a second save', async () => {
  const report = { id: 'report1', symbol: '000001.SZ', content: '研究正文', created_at: '2026-09-30' }
  mocks.list.mockResolvedValue({ reports: [report] })
  mocks.stream.mockImplementation(async function* () {
    yield { type: 'run', run_id: 'run1' }
    yield { type: 'delta', content: report.content }
    yield { type: 'done', report }
  })
  const store = await import('./stockAnalysisStore')
  await store.startAnalysis('000001.SZ', '平安银行')
  await vi.waitFor(() => expect(store.useDialogTask().task).toMatchObject({ phase: 'done', savedReportId: 'report1' }))
  expect(store.useHistoryReports().reports).toEqual([report])
  expect(mocks.save).not.toHaveBeenCalled()
})

test('old stream protocol remains archivable after a terminal event', async () => {
  mocks.save.mockResolvedValue({ report: { id: 'legacy1', symbol: '000001.SZ' } })
  mocks.stream.mockImplementation(async function* () {
    yield { type: 'delta', content: '旧协议正文' }
    yield { type: 'done' }
  })
  const store = await import('./stockAnalysisStore')
  await store.startAnalysis('000001.SZ', '平安银行')
  await vi.waitFor(() => expect(mocks.save).toHaveBeenCalledOnce())
})

test('retrying archived research preserves its chosen skills', async () => {
  mocks.stream.mockImplementation(async function* () { yield { type: 'error', message: 'test stops before LLM' } })
  const store = await import('./stockAnalysisStore')
  await store.retryAnalysis({ symbol: '000001.SZ', name: '平安银行', focus: '', mode: 'full', skill_ids: ['event_driven', 'growth_quality'] })
  await vi.waitFor(() => expect(mocks.stream).toHaveBeenCalledWith('000001.SZ', '', 'full', ['event_driven', 'growth_quality']))
})

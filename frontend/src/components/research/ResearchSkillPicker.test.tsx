// @vitest-environment jsdom
import { act, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { ResearchSkillPicker } from './ResearchSkillPicker'

const mocks = vi.hoisted(() => ({ catalog: vi.fn(), detail: vi.fn() }))
vi.mock('@/lib/api', () => ({ api: { researchSkills: mocks.catalog, researchSkillDetail: mocks.detail } }))

const catalog = ['趋势', '事件', '成长', '波浪'].map((name, i) => ({
  name: `skill_${i}`, display_name: name, category: 'framework', source: 'builtin',
  description: '研究定义', user_invocable: true, default_active: i === 0,
  aliases: [], required_tools: [], market_regimes: [],
}))
let cleanup = async () => {}
beforeEach(() => {
  vi.clearAllMocks()
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  mocks.catalog.mockResolvedValue({ skills: catalog, errors: [], builtin_count: 4, custom_count: 0 })
  mocks.detail.mockResolvedValue({ ...catalog[0], instructions: '**完整定义**：核对趋势与反证。' })
})
afterEach(async () => { await cleanup() })

async function render(disabled = false) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  function Harness() {
    const [selected, select] = useState<string[]>(['auto'])
    return <><ResearchSkillPicker value={selected} onChange={select} disabled={disabled} /><output>{JSON.stringify(selected)}</output></>
  }
  cleanup = async () => { await act(async () => root.unmount()); client.clear(); host.remove() }
  await act(async () => root.render(<QueryClientProvider client={client}><Harness /></QueryClientProvider>))
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
  return host
}

test('selecting replaces auto, caps at three and permits removing an existing skill', async () => {
  const host = await render()
  const boxes = [...host.querySelectorAll<HTMLInputElement>('input[type=checkbox]')]
  expect(boxes).toHaveLength(4)
  for (const box of boxes.slice(0, 3)) await act(async () => box.click())
  expect(host.querySelector('output')!.textContent).toBe('["skill_0","skill_1","skill_2"]')
  expect(boxes[3].disabled).toBe(true)
  await act(async () => boxes[1].click())
  expect(boxes[3].disabled).toBe(false)
  expect(host.querySelector('output')!.textContent).toBe('["skill_0","skill_2"]')
})

test('full definition is fetched only when opened and can be closed', async () => {
  const host = await render()
  expect(mocks.detail).not.toHaveBeenCalled()
  await act(async () => host.querySelector<HTMLButtonElement>('[aria-label="查看 趋势 的定义"]')!.click())
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
  expect(mocks.detail).toHaveBeenCalledWith('skill_0')
  expect(host.querySelector('[role=dialog]')!.textContent).toContain('完整定义')
  await act(async () => host.querySelector<HTMLButtonElement>('[aria-label="关闭"]')!.click())
  expect(host.querySelector('[role=dialog]')).toBeNull()
})

test('disabled picker prevents changing selection', async () => {
  const host = await render(true)
  const boxes = [...host.querySelectorAll<HTMLInputElement>('input[type=checkbox]')]
  expect(boxes.every(box => box.disabled)).toBe(true)
  await act(async () => boxes[0].click())
  expect(host.querySelector('output')!.textContent).toBe('["auto"]')
})

test('catalog errors show a retry action', async () => {
  mocks.catalog.mockRejectedValue(new Error('unavailable'))
  const host = await render()
  expect(host.querySelector('[role=alert]')!.textContent).toContain('Skill 目录加载失败')
  expect(host.querySelector('[role=alert] button')!.textContent).toBe('重试')
})

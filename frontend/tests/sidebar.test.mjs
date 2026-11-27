import assert from 'node:assert/strict'
import test from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

test('sidebar starts open on desktop and closed on small screens', async () => {
  const server = await createServer({ server: { middlewareMode: true, hmr: false }, appType: 'custom' })
  const oldWindow = globalThis.window
  const oldStorage = globalThis.localStorage
  try {
    const { RecognitionWorkspace } = await server.ssrLoadModule('/src/RecognitionWorkspace.tsx')
    const { LocaleContext, useLanguageState } = await server.ssrLoadModule('/src/localization.ts')
    globalThis.localStorage = { getItem: () => 'en' }
    function Provider({ children }) {
      return React.createElement(LocaleContext.Provider, { value: useLanguageState() }, children)
    }
    function render(smallScreen, language = 'en') {
      globalThis.localStorage = { getItem: () => language }
      globalThis.window = { matchMedia: () => ({ matches: smallScreen }) }
      return renderToStaticMarkup(React.createElement(Provider, null,
        React.createElement(RecognitionWorkspace, { apiStatus: 'connected', onLogout() {} })))
    }
    const desktop = render(false)
    assert.match(desktop, /id="sidebar" class="sidebar">/)
    assert.match(desktop, /class="sidebar-bottom".*aria-expanded="true" aria-label="Hide sidebar"/)
    const mobile = render(true)
    assert.match(mobile, /id="sidebar" class="sidebar" hidden=""/)
    assert.match(mobile, /class="topbar-leading".*aria-expanded="false" aria-label="Show sidebar"/)
    for (const [language, heading, schedules, reports, records, rules, cameras, upload] of [
      ['en', 'Monitoring overview', 'Automatic AI checks', 'Inspection center', 'Recognition records', 'Checking rules', 'Camera configuration', 'Upload screenshot'],
      ['zh-Hant', '監察總覽', '自動 AI 檢查', '檢查中心', '辨識紀錄', '檢查規則', '攝影機設定', '上傳截圖'],
      ['zh-Hans', '监控总览', '自动 AI 检查', '检查中心', '识别记录', '检查规则', '摄像机配置', '上传截图'],
    ]) {
      const html = render(false, language)
      assert.ok(html.includes(`<h1>${heading}</h1>`))
      assert.match(html, /BEST MART <span class="brand-accent">360°<\/span>/)
      assert.ok(html.includes('RetailVision ·'))
      const nav = html.match(/<nav[^>]*>(.*?)<\/nav>/)[1]
      const positions = [heading, schedules, reports, records, rules, cameras].map(label => nav.indexOf(`title="${label}"`))
      assert.ok(positions.every(position => position >= 0))
      assert.deepEqual(positions, [...positions].sort((a, b) => a - b))
      assert.match(nav, /aria-current="page" title=/)
      assert.ok(!html.includes(`>${upload}</button>`), 'manual upload is not a home-screen action')
      assert.match(html, /class="monitoring-flow"/)
      assert.doesNotMatch(html, /class="records-panel"/)
    }
  } finally {
    if (oldWindow === undefined) delete globalThis.window
    else globalThis.window = oldWindow
    if (oldStorage === undefined) delete globalThis.localStorage
    else globalThis.localStorage = oldStorage
    await server.close()
  }
})

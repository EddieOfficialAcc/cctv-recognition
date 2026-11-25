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
    function render(smallScreen) {
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
  } finally {
    if (oldWindow === undefined) delete globalThis.window
    else globalThis.window = oldWindow
    if (oldStorage === undefined) delete globalThis.localStorage
    else globalThis.localStorage = oldStorage
    await server.close()
  }
})

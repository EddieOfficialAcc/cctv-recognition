import assert from 'node:assert/strict'
import test from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

test('login offers an opt-in remembered session in every language with password-manager autofill', async () => {
  const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
  const oldStorage = globalThis.localStorage
  try {
    const { LoginScreen } = await server.ssrLoadModule('/src/components/LoginScreen.tsx')
    const { LocaleContext, useLanguageState } = await server.ssrLoadModule('/src/localization.ts')
    for (const [language, label] of [
      ['en', 'Remember me for 30 days'], ['zh-Hant', '記住我 30 天'], ['zh-Hans', '记住我 30 天'],
    ]) {
      globalThis.localStorage = { getItem: () => language }
      function Provider({ children }) {
        return React.createElement(LocaleContext.Provider, { value: useLanguageState() }, children)
      }
      const html = renderToStaticMarkup(React.createElement(Provider, null,
        React.createElement(LoginScreen, { onLogin() {}, sessionError: false })))
      assert.ok(html.includes(label))
      assert.match(html, /type="checkbox"[^>]*aria-describedby="remember-me-hint"/)
      assert.doesNotMatch(html, /type="checkbox"[^>]*checked/)
      assert.match(html, /autoComplete="current-password"/)
    }
  } finally {
    if (oldStorage === undefined) delete globalThis.localStorage
    else globalThis.localStorage = oldStorage
    await server.close()
  }
})

test('login API sends the selected remember-me value and keeps authentication errors explicit', async () => {
  const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
  const oldFetch = globalThis.fetch
  try {
    const { authRequest, AuthError } = await server.ssrLoadModule('/src/api.ts')
    for (const remember_me of [false, true]) {
      globalThis.fetch = async (url, options) => {
        assert.equal(url, '/api/auth/login')
        assert.equal(options.method, 'POST')
        assert.equal(options.headers['X-Requested-With'], 'XMLHttpRequest')
        assert.deepEqual(JSON.parse(options.body), { username: 'operator', password: 'test-password', remember_me })
        return Response.json({ username: 'operator' })
      }
      await authRequest('login', { username: 'operator', password: 'test-password', remember_me })
    }
    globalThis.fetch = async () => new Response(null, { status: 401 })
    await assert.rejects(authRequest('login', { username: 'operator', password: 'wrong', remember_me: true }),
      error => error instanceof AuthError && error.status === 401)
  } finally {
    globalThis.fetch = oldFetch
    await server.close()
  }
})

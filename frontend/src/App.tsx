import { useEffect, useState } from 'react'
import { LoginScreen } from './components/LoginScreen'
import { authRequest, AuthError } from './api'
import { useLocale } from './localization'
import { translate } from './messages'
import { RecognitionWorkspace } from './RecognitionWorkspace'

type ApiStatus = 'checking' | 'connected' | 'unavailable'

function App() {
  const [apiStatus, setApiStatus] = useState<ApiStatus>('checking')
  const [authState, setAuthState] = useState<'checking' | 'login' | 'workspace'>('checking')
  const [sessionError, setSessionError] = useState(false)
  const { language } = useLocale()

  useEffect(() => {
    if (authState !== 'workspace') document.title = `${translate(language, 'loginTitle')} | RetailVision`
  }, [language, authState])

  useEffect(() => {
    const controller = new AbortController()
    void authRequest('session', undefined, controller.signal).then(() => setAuthState('workspace')).catch(error => {
      if (controller.signal.aborted) return
      if (!(error instanceof AuthError && error.status === 401)) {
        console.error('Session check failed:', error)
        setSessionError(true)
      }
      setAuthState('login')
    })
    return () => controller.abort()
  }, [])

  useEffect(() => {
    function expired() { setAuthState('login') }
    window.addEventListener('cctv-session-expired', expired)
    return () => window.removeEventListener('cctv-session-expired', expired)
  }, [])

  useEffect(() => {
    const controller = new AbortController()

    async function checkApi() {
      try {
        const response = await fetch('/api/health', { signal: controller.signal })
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        const data: unknown = await response.json()
        if (
          typeof data !== 'object' ||
          data === null ||
          !('status' in data) ||
          data.status !== 'ok'
        ) {
          throw new Error('Unexpected health response')
        }
        setApiStatus('connected')
      } catch (error) {
        if (!controller.signal.aborted) {
          console.error('API health check failed:', error)
          setApiStatus('unavailable')
        }
      }
    }

    void checkApi()
    return () => controller.abort()
  }, [])

  if (authState === 'checking') return <main className="login-loading" role="status">{translate(language, 'loginChecking')}</main>
  return authState === 'workspace'
    ? <RecognitionWorkspace apiStatus={apiStatus} onLogout={() => setAuthState('login')} />
    : <LoginScreen sessionError={sessionError} onLogin={() => { setSessionError(false); setAuthState('workspace') }} />
}

export default App

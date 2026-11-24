import { useEffect, useState } from 'react'
import { RecognitionWorkspace } from './RecognitionWorkspace'

type ApiStatus = 'checking' | 'connected' | 'unavailable'

function App() {
  const [apiStatus, setApiStatus] = useState<ApiStatus>('checking')

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

  return <RecognitionWorkspace apiStatus={apiStatus} />
}

export default App

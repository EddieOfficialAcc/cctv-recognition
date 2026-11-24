import { useEffect, useState } from 'react'

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

  const statusText = {
    checking: 'Checking API connection...',
    connected: 'API connected',
    unavailable: 'API unavailable. Make sure the backend is running.',
  }[apiStatus]

  return (
    <main className="mx-auto max-w-4xl px-6 py-16">
      <p className="text-sm font-semibold uppercase tracking-wide text-teal-700">
        CCTV Recognition
      </p>
      <h1 className="mt-4 text-4xl font-bold tracking-tight text-slate-900">
        Retail CCTV inspection
      </h1>
      <p className="mt-4 max-w-2xl text-lg text-slate-600">
        Development foundation for monitoring camera health and reviewing
        inspection exceptions.
      </p>

      <section className="mt-10 rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900">System status</h2>
        <p
          className={`mt-2 text-sm ${apiStatus === 'unavailable' ? 'text-red-700' : 'text-slate-600'}`}
          role="status"
        >
          {statusText}
        </p>
        <p className="mt-4 text-sm text-slate-600">
          This starter does not yet connect to cameras or perform inspections.
        </p>
      </section>
    </main>
  )
}

export default App

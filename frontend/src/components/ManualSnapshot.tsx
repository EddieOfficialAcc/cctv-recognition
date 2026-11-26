import { useEffect, useRef, useState, type FormEvent } from 'react'
import { ApiError, captureCameraSnapshot, fetchSnapshotRecorders, type SnapshotRecorder } from '../api'
import { useLocale } from '../localization'
import { Dialog } from './Dialog'
import { EvidenceImage } from './EvidenceImage'

declare global {
  interface Window {
    showSaveFilePicker?: (options: { suggestedName: string }) => Promise<{
      createWritable(): Promise<FileSystemWritableFileStream>
    }>
  }
}

export function ManualSnapshot({ recorder, onClose }: {
  recorder?: SnapshotRecorder; onClose: () => void
}) {
  const { t } = useLocale()
  const [recorders, setRecorders] = useState<SnapshotRecorder[] | null>(null)
  const [recorderError, setRecorderError] = useState(false)
  const [selection, setSelection] = useState('')
  const [channel, setChannel] = useState(1)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<'capture' | 'save' | 'configuration' | null>(null)
  const [preview, setPreview] = useState<{ url: string; filename: string; camera: string } | null>(null)
  const active = useRef(false)
  const url = useRef<string | null>(null)
  useEffect(() => {
    active.current = true
    return () => {
      active.current = false
      if (url.current) URL.revokeObjectURL(url.current)
    }
  }, [])
  useEffect(() => {
    if (recorder) return
    const controller = new AbortController()
    void fetchSnapshotRecorders(controller.signal).then(result => {
      if (!controller.signal.aborted) setRecorders(result)
    }).catch(cause => {
      if (!controller.signal.aborted) {
        console.error('Snapshot recorder list failed to load:', cause)
        setRecorderError(true)
      }
    })
    return () => controller.abort()
  }, [recorder])
  const selected = recorder ?? (selection === '' ? undefined : recorders?.[Number(selection)])

  async function capture(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!selected) return
    const { shop, region } = selected
    setBusy(true); setError(null)
    const filename = `snapshot-${shop.replace(/[^a-z0-9_-]/gi, '_')}-channel-${channel}-${Date.now()}.jpg`
    try {
      let destination = null
      try {
        destination = window.showSaveFilePicker ? await window.showSaveFilePicker({ suggestedName: filename }) : null
      } catch (cause) {
        if (cause instanceof DOMException && cause.name === 'AbortError') return
        throw cause
      }
      let image: Blob
      try {
        image = await captureCameraSnapshot(shop, region, channel)
      } catch (cause) {
        console.error('Manual snapshot capture failed:', cause)
        if (active.current) setError(cause instanceof ApiError && cause.code === 'snapshotConfigurationError' ? 'configuration' : 'capture')
        return
      }
      if (!active.current) return
      if (url.current) URL.revokeObjectURL(url.current)
      const imageUrl = URL.createObjectURL(image)
      url.current = imageUrl
      setPreview({ url: imageUrl, filename, camera: `${shop} · ${channel}` })
      if (destination) {
        const file = await destination.createWritable()
        try {
          await file.write(image)
          await file.close()
        } catch (cause) {
          await file.abort().catch(abortError => console.error('Snapshot file could not be aborted:', abortError))
          throw cause
        }
      } else {
        const link = document.createElement('a')
        link.href = imageUrl
        link.download = filename
        link.click()
      }
    } catch (cause) {
      console.error('Manual snapshot save failed:', cause)
      if (active.current) setError('save')
    } finally {
      if (active.current) setBusy(false)
    }
  }

  return <Dialog title={t('snapshotCapture')} subtitle={recorder?.shop ?? 'DAHAU'} onClose={onClose} busy={busy}>
    <form className="dialog-body" id="dialog-description" onSubmit={capture}>
      {!recorder && <label>{t('snapshotRecorder')}<select value={selection} disabled={busy || !recorders?.length}
        onChange={event => { setSelection(event.target.value); setChannel(1); setError(null) }}>
        <option value="">{t(recorderError ? 'loadErrorTitle' : !recorders ? 'loadingTitle' :
          recorders.length ? 'snapshotChooseRecorder' : 'snapshotNoRecorders')}</option>
        {recorders?.map(({ shop, name, region }, index) =>
          <option key={index} value={index}>{[shop, name, region].filter(Boolean).join(' · ')}</option>)}
      </select></label>}
      {recorderError && <p className="error-message" role="alert">{t('snapshotRecorderError')}</p>}
      <label>{t('cameraChannel')}<select value={selected ? channel : ''} disabled={busy || !selected}
        onChange={event => setChannel(Number(event.target.value))}>
        {!selected && <option value="">—</option>}
        {Array.from({ length: Math.min(selected?.channels ?? 0, 256) }, (_, index) =>
          <option key={index + 1} value={index + 1}>{index + 1}</option>)}
      </select></label>
      <p className="field-hint">{t('snapshotFolderHint')}</p>
      <button className="button primary" disabled={busy || !selected}>{t(busy ? 'loadingTitle' : 'snapshotCaptureDownload')}</button>
      {error && <p className="error-message" role="alert">{t(error === 'configuration' ? 'snapshotConfigurationError' :
        error === 'capture' ? 'snapshotCaptureError' : 'snapshotSaveError')}</p>}
      {preview && <div>
        <EvidenceImage key={preview.url} src={preview.url} alt={t('evidenceAlt', { camera: preview.camera })} />
        <a className="text-button" href={preview.url} download={preview.filename}>{t('reportEvidence')}</a>
      </div>}
    </form>
  </Dialog>
}

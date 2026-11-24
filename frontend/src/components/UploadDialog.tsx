import { useEffect, useRef, useState, type FormEvent } from 'react'
import { uploadScreenshot, type RecognitionRecord } from '../api'
import { Dialog } from './Dialog'
import { Icon } from './Icon'
import { useLocale } from '../localization'
import type { MessageKey } from '../messages'

export function UploadDialog({ onClose, onUploaded }: {
  onClose: () => void; onUploaded: (record: RecognitionRecord) => void
}) {
  const { t, errorMessage } = useLocale()
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState('')
  const [dragging, setDragging] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | MessageKey | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)

  useEffect(() => {
    return () => { if (preview) URL.revokeObjectURL(preview) }
  }, [preview])

  function chooseFile(selected?: File) {
    if (!selected || busy) return
    setError(null)
    if (!['image/jpeg', 'image/png', 'image/webp'].includes(selected.type)) {
      setError('fileTypeError')
      return
    }
    if (selected.size === 0 || selected.size > 10 * 1024 * 1024) {
      setError('fileSizeError')
      return
    }
    setFile(selected)
    setPreview(URL.createObjectURL(selected))
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!file || busy) return
    const body = new FormData(event.currentTarget)
    body.set('screenshot', file)
    const captured = body.get('captured_at')
    if (typeof captured === 'string' && captured) {
      body.set('captured_at', `${captured}:00+08:00`)
    } else body.delete('captured_at')
    setBusy(true)
    setError(null)
    try {
      onUploaded(await uploadScreenshot(body))
    } catch (cause) {
      console.error('Screenshot upload failed:', cause)
      setError(cause instanceof Error ? cause : new Error(String(cause)))
    } finally {
      setBusy(false)
    }
  }

  return <Dialog title={t('uploadScreenshot')} subtitle={t('newInspection')} onClose={onClose} busy={busy}>
    <form onSubmit={submit} onInvalid={event => {
      const input = event.target
      if (input instanceof HTMLInputElement) {
        input.setCustomValidity(t(input.validity.valueMissing ? 'requiredFieldError' : 'invalidFieldError'))
      }
    }} onInput={event => {
      if (event.target instanceof HTMLInputElement) event.target.setCustomValidity('')
    }}>
      <div className="dialog-body">
        <p id="dialog-description" className="muted">{t('uploadDescription')}</p>
        <input ref={fileInput} type="file" accept="image/jpeg,image/png,image/webp" className="sr-only"
          aria-label={t('chooseScreenshot')} disabled={busy} onChange={event => {
            chooseFile(event.target.files?.[0])
            event.target.value = ''
          }} />
        <button type="button" disabled={busy} className={`dropzone ${dragging ? 'dragging' : ''}`}
          onClick={() => fileInput.current?.click()} onDragOver={event => {
            event.preventDefault(); if (!busy) setDragging(true)
          }} onDragLeave={() => setDragging(false)} onDrop={event => {
            event.preventDefault(); setDragging(false)
            if (event.dataTransfer.files.length !== 1) setError('oneFileError')
            else chooseFile(event.dataTransfer.files[0])
          }}>
          {file ? <>{preview && <img src={preview} alt={t('previewAlt')} />}<strong>{file.name}</strong>
            <span>{t('replaceFile', { size: (file.size / 1024 / 1024).toFixed(2) })}</span></> :
            <><span className="upload-symbol"><Icon name="upload" size={26} /></span>
              <strong>{t('dropzone')}</strong><span>{t('fileLimits')}</span></>}
        </button>
        <fieldset disabled={busy} className="form-grid">
          <label>{t('shopCode')} <span className="required">*</span>
            <input name="shop_code" placeholder={t('shopPlaceholder')} required maxLength={50} pattern=".*\S.*" />
          </label>
          <label>{t('cameraChannel')} <span className="required">*</span>
            <input name="camera_name" placeholder={t('cameraPlaceholder')} required maxLength={100} pattern=".*\S.*" />
          </label>
          <label>{t('region')} <span className="required">*</span>
            <select name="region" defaultValue="Hong Kong"><option value="Hong Kong">{t('hongKong')}</option><option value="Macau">{t('macau')}</option></select>
          </label>
          <label>{t('captureTime')} <span className="optional">{t('optional')}</span>
            <input name="captured_at" type="datetime-local" />
            <span className="field-hint">{t('captureTimeHint')}</span>
          </label>
        </fieldset>
        <div className="notice"><Icon name="info" /><p>{t('uploadLimitations')}</p></div>
        {error && <div className="error-message" role="alert">{errorMessage(error)}</div>}
      </div>
      <footer className="dialog-footer">
        <button className="button secondary" type="button" disabled={busy} onClick={onClose}>{t('cancel')}</button>
        <button className="button primary" type="submit" disabled={!file || busy}>
          {busy ? <span className="spinner" /> : <Icon name="upload" size={17} />}
          {t(busy ? 'analyzing' : 'analyzeSave')}
        </button>
      </footer>
    </form>
  </Dialog>
}

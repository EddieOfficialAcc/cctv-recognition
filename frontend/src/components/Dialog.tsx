import { useEffect, useRef, type ReactNode } from 'react'
import { Icon } from './Icon'
import { useLocale } from '../localization'

export function Dialog({ title, subtitle, children, onClose, drawer = false, busy = false }: {
  title: string; subtitle: string; children: ReactNode; onClose: () => void
  drawer?: boolean; busy?: boolean
}) {
  const { t } = useLocale()
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const dialog = ref.current
    dialog?.showModal()
    return () => dialog?.close()
  }, [])

  return <dialog ref={ref} className={drawer ? 'dialog drawer' : 'dialog'}
    aria-labelledby="dialog-title" aria-describedby="dialog-description"
    onCancel={event => { event.preventDefault(); if (!busy) onClose() }}>
    <div className="dialog-header">
      <div><p className="eyebrow">{subtitle}</p><h2 id="dialog-title">{title}</h2></div>
      <button className="icon-button" type="button" aria-label={t('closeDialog')} disabled={busy} onClick={onClose}>
        <Icon name="close" />
      </button>
    </div>
    {children}
  </dialog>
}

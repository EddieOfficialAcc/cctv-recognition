import { useState } from 'react'
import { burstFrameUrl, evidenceUrl } from '../api'
import { Icon } from './Icon'
import { useLocale } from '../localization'

export function EvidenceImage({ alt, thumbnail = false, errorText, ...source }: {
  alt: string; thumbnail?: boolean; errorText?: string
} & ({ id: string; frameIndex?: number } | { src: string })) {
  const { t } = useLocale()
  const [failed, setFailed] = useState(false)
  return <div className={thumbnail ? 'evidence-thumbnail' : 'evidence-image'}>
    {failed ? <span className="image-failure"><Icon name="image" />
      {!thumbnail && (errorText ?? t('evidenceError'))}</span> :
      <img src={'src' in source ? source.src : source.frameIndex ? burstFrameUrl(source.id, source.frameIndex) : evidenceUrl(source.id)}
        alt={alt} loading={thumbnail ? 'lazy' : 'eager'}
        onError={() => setFailed(true)} />}
  </div>
}

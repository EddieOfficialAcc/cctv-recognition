import { useEffect, useState } from 'react'
import { fetchCameraConfiguration, type CameraBrandFilter, type CameraCompanyFilter, type CameraConfigurationPage, type Region, type SnapshotRecorder } from '../api'
import { useLocale } from '../localization'
import { Icon } from './Icon'
import { ManualSnapshot } from './ManualSnapshot'

export function CameraConfiguration() {
  const { t, formatDate, errorMessage } = useLocale()
  const [data, setData] = useState<CameraConfigurationPage | null>(null)
  const [page, setPage] = useState(1)
  const [brand, setBrand] = useState<CameraBrandFilter>('')
  const [region, setRegion] = useState<'' | Region>('')
  const [company, setCompany] = useState<CameraCompanyFilter>('')
  const [refresh, setRefresh] = useState(0)
  const [updated, setUpdated] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<Error | null>(null)
  const [capture, setCapture] = useState<'choose' | SnapshotRecorder | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    void fetchCameraConfiguration(page, controller.signal, brand, region, company).then(result => {
      if (controller.signal.aborted) return
      const lastPage = Math.max(1, Math.ceil(result.total / result.page_size))
      if (page > lastPage) { setPage(lastPage); return }
      setData(result)
      setUpdated(new Date().toISOString())
      setLoading(false)
    }).catch(cause => {
      if (controller.signal.aborted) return
      console.error('Camera configuration failed to load:', cause)
      setError(cause instanceof Error ? cause : new Error(String(cause)))
      setLoading(false)
    })
    return () => controller.abort()
  }, [page, refresh, brand, region, company])

  function reload() {
    setLoading(true)
    setError(null)
    setRefresh(value => value + 1)
  }

  function changePage(next: number) {
    setLoading(true)
    setError(null)
    setPage(next)
  }

  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1
  return <>
    <div className="page-heading">
      <div><p className="eyebrow">{t('operations')}</p><h1>{t('cameraConfiguration')}</h1>
        <p className="page-description">{t('configurationDescription')}</p></div>
      <div className="configuration-actions">
        <button className="button primary" onClick={() => setCapture('choose')}>
          <Icon name="camera" size={16} />{t('snapshotCapture')}
        </button>
        <button className="button secondary" disabled={loading} onClick={reload}>
          <Icon name="refresh" size={16} />{t('refresh')}
        </button>
      </div>
    </div>
    <section className="records-panel" aria-labelledby="config-title">
      <div className="panel-heading">
        <div className="section-heading"><h2 id="config-title">{t('cameraConfiguration')}</h2></div>
        <span>{updated ? t('configurationUpdated', { time: formatDate(updated, true) }) : ''}</span>
      </div>
      <div className="filter-bar">
        <label><span className="sr-only">{t('configurationFilterBrand')}</span>
          <select value={brand} onChange={event => {
            const next = event.target.value
            if (next !== '' && next !== 'DAHAU' && next !== 'HIKVISION') return
            if (next === brand) return
            setData(null)
            changePage(1)
            setBrand(next)
          }}>
            <option value="">{t('configurationAllBrands')}</option>
            <option value="DAHAU">DAHAU</option>
            <option value="HIKVISION">HIKVISION</option>
          </select>
        </label>
        <label><span className="sr-only">{t('filterRegion')}</span>
          <select value={region} onChange={event => {
            const next = event.target.value
            if (next !== '' && next !== 'Hong Kong' && next !== 'Macau') return
            if (next === region) return
            setData(null)
            changePage(1)
            setRegion(next)
          }}>
            <option value="">{t('allRegions')}</option>
            <option value="Hong Kong">{t('hongKong')}</option>
            <option value="Macau">{t('macau')}</option>
          </select>
        </label>
        <label><span className="sr-only">{t('configurationFilterCompany')}</span>
          <select value={company} onChange={event => {
            const next = event.target.value
            if (next !== '' && next !== 'BestMart360' && next !== 'FoodVilla') return
            if (next === company) return
            setData(null)
            changePage(1)
            setCompany(next)
          }}>
            <option value="">{t('configurationAllCompanies')}</option>
            <option value="BestMart360">BestMart360</option>
            <option value="FoodVilla">FoodVilla</option>
          </select>
        </label>
        {(brand || region || company) && <button className="text-button" onClick={() => {
          setData(null)
          changePage(1)
          setBrand('')
          setRegion('')
          setCompany('')
        }}>{t('reset')}</button>}
      </div>
      <div className="table-container" aria-busy={loading}>
        {loading ? <div className="empty-state" role="status">{t('loadingTitle')}</div> :
          error ? <div className="empty-state error-state" role="alert">
            <h3>{t('loadErrorTitle')}</h3><p>{errorMessage(error)}</p>
            <button className="button secondary" onClick={reload}>{t('tryAgain')}</button>
          </div> : data?.rows.length ? <table>
            <caption className="sr-only">{t('cameraConfiguration')}</caption>
            <thead><tr><th scope="col">{t('shopCode')}</th><th scope="col">{t('configurationShopName')}</th>
              <th scope="col">{t('configurationCompany')}</th><th scope="col">{t('region')}</th>
              <th scope="col">{t('configurationBrand')}</th><th scope="col">{t('configurationInternalIp')}</th>
              <th scope="col">{t('configurationPort')}</th><th scope="col">{t('configurationChannels')}</th>
              <th scope="col">{t('configurationAdminName')}</th><th scope="col">{t('configurationAdminPassword')}</th>
              <th scope="col">{t('configurationCredentials')}</th><th scope="col">{t('actions')}</th></tr></thead>
            <tbody>{data.rows.map((row, index) => <tr key={`${row.shop_code}-${index}`}>
              <td>{row.shop_code ?? '—'}</td><td>{row.shop_name ?? '—'}</td><td>{row.company ?? '—'}</td><td>{row.region ?? '—'}</td>
              <td>{row.brand ?? '—'}</td><td>{row.internal_ip ?? '—'}</td><td>{row.port ?? '—'}</td>
              <td>{row.cam_qty ?? '—'}</td>
              <td>{row.credentials_complete ? <span aria-label={t('configurationValueHidden')}>••••••</span> : '—'}</td>
              <td>{row.credentials_complete ? <span aria-label={t('configurationValueHidden')}>••••••</span> : '—'}</td>
              <td>{t(row.credentials_complete ? 'configurationComplete' : 'configurationIncomplete')}</td>
              <td>{row.brand === 'DAHAU' && row.shop_code && row.region && row.internal_ip &&
                row.cam_qty !== null && row.cam_qty > 0 &&
                <button className="text-button" onClick={() => {
                  if (row.shop_code && row.region && row.cam_qty) {
                    setCapture({ shop: row.shop_code, region: row.region, name: row.shop_name, channels: row.cam_qty })
                  }
                }}>{t('snapshotCapture')}</button>}</td>
            </tr>)}</tbody>
          </table> : <div className="empty-state">{t('configurationEmpty')}</div>}
      </div>
      <footer className="table-footer">
        <span>{data && !error ? t('recordCount', { count: data.total }) : ''}</span>
        <div className="pagination">
          <button className="icon-button" aria-label={t('previousPage')} disabled={loading || !!error || page === 1}
            onClick={() => changePage(page - 1)}><Icon name="chevron" size={16} style={{ transform: 'rotate(180deg)' }} /></button>
          <span>{t('pageOf', { page, total: totalPages })}</span>
          <button className="icon-button" aria-label={t('nextPage')} disabled={loading || !!error || page >= totalPages}
            onClick={() => changePage(page + 1)}><Icon name="chevron" size={16} /></button>
        </div>
      </footer>
    </section>
    {capture && <ManualSnapshot recorder={capture === 'choose' ? undefined : capture} onClose={() => setCapture(null)} />}
  </>
}

import { Segmented } from '../../../ui'
import { BUCKET_OPTIONS, PERIOD_OPTIONS, type DashboardParams } from './params'

/**
 * One filter row, above everything it scopes.
 *
 * Not per-chart, and never inside a chart card: every plot below re-renders against the
 * same slice, which is the only reason the numbers on this screen can be trusted to agree
 * with each other. The row wraps on a phone rather than scrolling sideways — a control the
 * reader has to discover by swiping is a control that doesn't get used.
 *
 * The demo switch appears only when there is demo data to switch. A toggle whose two states
 * are identical is chrome.
 */
export function Filters({ params }: { params: DashboardParams }) {
  return (
    <div className="dash-filters" role="group" aria-label="Filtri del riepilogo">
      <Segmented
        options={PERIOD_OPTIONS}
        value={params.period}
        onChange={params.setPeriod}
        label="Periodo"
      />
      <Segmented
        options={BUCKET_OPTIONS}
        value={params.bucket}
        onChange={params.setBucket}
        label="Raggruppamento"
      />
      {params.demoEntries > 0 && (
        <Segmented
          options={[
            { value: 'on', label: 'con demo', title: 'Includi i dati dimostrativi' },
            { value: 'off', label: 'solo reali', title: 'Escludi i dati dimostrativi' },
          ]}
          value={params.includeDemo ? 'on' : 'off'}
          onChange={params.setDemo}
          label="Dati dimostrativi"
        />
      )}
    </div>
  )
}

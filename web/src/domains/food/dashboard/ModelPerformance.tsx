import { useQuery } from '@tanstack/react-query'

import { api, type RiskEvaluation, type RiskModelMetrics } from '../../../api'
import { Badge, Card, EmptyState, Icon, Skeleton } from '../../../ui'
import { ChartFrame } from '../../../ui/charts'

/**
 * View 4: how well the "a rischio di spreco" flag actually works.
 *
 * This screen exists to make the predictive module falsifiable, so it is written to report a
 * bad result as readily as a good one. Three things about the current state are true and are
 * shown as headlines rather than footnotes:
 *
 *  - **The data gate is not met.** v1 needs 150 resolved items and 40 waste events; there
 *    are 25 waste events even counting the synthetic household, and 0 without it. When the
 *    gate is unmet that *is* the finding, so it renders above the metrics, not below them.
 *  - **v1 did not beat v0.** The Cox fit scores below the heuristic it was meant to improve
 *    on. Nothing here visually favours the newer model, and the losing model is not hidden.
 *  - **Every number is demo-derived.** The warning is a persistent band inside the card, not
 *    a tooltip, because a precision figure computed from a generator is not a measurement.
 *
 * DELIBERATELY NOT A CHART. Three or four metrics across at most three models is a table —
 * there is no time axis, and a grouped bar chart of four numbers would add a legend, an axis
 * and a colour decision to say exactly what the digits already say. A confusion matrix gets
 * a 2×2 grid because that is the most legible form precision and recall have. Both still sit
 * in a `ChartFrame` so they inherit the same title/caption/table-twin contract as the plots.
 *
 * SMALL n IS PART OF THE CLAIM. 0.38 from 8 items is not the same assertion as 0.38 from
 * 800, so every rate is rendered beside the count it came from.
 */
export function ModelPerformance({ includeDemo }: { includeDemo: boolean }) {
  const { data, isPending, isError } = useQuery({
    queryKey: ['riskEvaluation', includeDemo],
    queryFn: () => api.riskEvaluation(includeDemo),
  })

  if (isPending) {
    return (
      <Card className="chart-card" title="Qualità della previsione">
        <Skeleton height="220px" radius="var(--radius)" />
      </Card>
    )
  }

  if (isError) {
    return (
      <Card className="chart-card" title="Qualità della previsione">
        <EmptyState
          icon="settings"
          title="Modulo di rischio non raggiungibile"
          description="Gli endpoint /api/risk non hanno risposto. Il resto del riepilogo non dipende da questa scheda."
        />
      </Card>
    )
  }

  const models = data.models ?? []

  // Nothing to evaluate. On real data this is the normal state until a few weeks of outcomes
  // exist, so it explains what has to happen rather than showing a table of dashes.
  if (models.length === 0 || data.total_rows === 0) {
    return (
      <Card className="chart-card" title="Qualità della previsione">
        <EmptyState
          icon="chart"
          title="Non c'è ancora niente da valutare"
          description={
            includeDemo
              ? "Nessun articolo risolto nel campione. Il rilevatore si misura contro quello che è successo davvero, quindi serve prima qualche settimana di consumi registrati."
              : "Nessun consumo reale registrato finora. Registra cosa finisce mangiato e cosa buttato per qualche settimana: è quello il metro con cui si misura il flag «a rischio». Con «con demo» attivo puoi vedere come si comporterà la scheda."
          }
        />
        <GateProgress gate={data.gate} />
      </Card>
    )
  }

  return (
    <ChartFrame
      title="Qualità della previsione"
      hint="Precision e recall del flag «a rischio», confrontati con la baseline v0."
      caption={<Caption data={data} />}
      table={{
        columns: ['Modello', 'C-index', 'Precision', 'Recall', 'F1', 'Valutato su'],
        rows: models.map((m) => [
          m.name,
          num(m.concordance),
          num(m.precision),
          num(m.recall),
          num(m.f1),
          `${m.evaluated} articoli`,
        ]),
      }}
    >
      {data.provenance.warning && (
        <p className="model-warning" role="note">
          <Icon name="warning" /> {data.provenance.warning}
        </p>
      )}

      {data.provenance.fitted_below_gate && (
        <p className="model-warning" role="note">
          <Icon name="warning" /> Questo modello è stato addestrato forzando il limite minimo
          di dati. I numeri sotto non sono affidabili.
        </p>
      )}

      <GateProgress gate={data.gate} />

      <Verdict data={data} />

      <div className="model-table" role="table" aria-label="Confronto fra modelli">
        <div className="model-row model-head" role="row">
          <span role="columnheader">Modello</span>
          <span role="columnheader">C-index</span>
          <span role="columnheader">Precision</span>
          <span role="columnheader">Recall</span>
          <span role="columnheader">F1</span>
        </div>
        {models.map((m) => (
          <div className="model-row" role="row" key={m.name}>
            <span role="cell">
              {m.name}
              {m.name === data.best_model && models.length > 1 && (
                <Badge tone="neutral">migliore</Badge>
              )}
            </span>
            {/* Every rate carries the count behind it: a recall of 0.33 over 9 positives is
                three items, and the reader should not have to go looking for that. */}
            <Metric value={m.concordance} note={`${m.ranked} ordinati`} />
            <Metric value={m.precision} note={`${m.flagged} segnalati`} />
            <Metric value={m.recall} note={`${m.positives} sprechi`} />
            <Metric value={m.f1} note={`${m.evaluated} valutati`} />
          </div>
        ))}
      </div>

      {models.map((m) => (
        <Confusion key={m.name} model={m} />
      ))}
    </ChartFrame>
  )
}

function num(v: number | null): string {
  return v === null ? '—' : v.toFixed(2)
}

function Metric({ value, note }: { value: number | null; note: string }) {
  return (
    <span role="cell" className="model-metric">
      <strong>{num(value)}</strong>
      <em>{note}</em>
    </span>
  )
}

/**
 * The gate, as two progress bars.
 *
 * Genuinely not a chart either — it is "how far along are two counters", which is what a
 * progress indicator is for. Shown even in the empty state, because "you need 40 waste
 * events and have 0" is the single most useful thing this card can say before there is data.
 */
function GateProgress({ gate }: { gate: RiskEvaluation['gate'] }) {
  const bars = [
    { label: 'Articoli risolti', have: gate.resolved, need: gate.min_resolved },
    { label: 'Sprechi registrati', have: gate.waste_events, need: gate.min_waste_events },
  ]

  return (
    <div className="gate">
      <div className="gate-head">
        <strong>{gate.met ? 'Dati sufficienti per il modello v1' : 'Dati non ancora sufficienti'}</strong>
        {!gate.met && <Badge tone="warn">v1 non addestrabile</Badge>}
      </div>
      {bars.map((b) => {
        const done = Math.min(1, b.need === 0 ? 1 : b.have / b.need)
        return (
          <div className="gate-bar" key={b.label}>
            <span className="gate-label">{b.label}</span>
            <span className="gate-track" aria-hidden="true">
              <span
                className={`gate-fill ${b.have >= b.need ? 'is-met' : ''}`}
                style={{ width: `${done * 100}%` }}
              />
            </span>
            <span className="gate-count">
              {b.have} / {b.need}
            </span>
          </div>
        )
      })}
    </div>
  )
}

/**
 * States the comparison in words.
 *
 * A reader who sees two c-indices and no sentence will assume the newer number is the better
 * one, because that is what a "v1" next to a "v0" normally means. Here it is not, and saying
 * so plainly is the whole point of publishing the comparison at all.
 */
function Verdict({ data }: { data: RiskEvaluation }) {
  if (!data.fitted) {
    return (
      <p className="model-verdict">
        Nessun modello v1 addestrato. Il punteggio in uso è l'euristica <strong>v0</strong>,
        che è anche la baseline con cui v1 dovrà essere confrontato.
      </p>
    )
  }

  if (data.v1_beats_v0 === false) {
    return (
      <p className="model-verdict">
        <strong>v1 non ha battuto v0.</strong> Il modello di sopravvivenza ordina gli articoli
        peggio dell'euristica su cui doveva migliorare, quindi il punteggio in uso resta v0.
        È un risultato, non un errore: un modello più complesso che non guadagna niente è
        un'informazione utile.
      </p>
    )
  }

  if (data.v1_beats_v0 === true) {
    return (
      <p className="model-verdict">
        v1 batte la baseline v0 sul periodo di test. Il confronto è su{' '}
        {data.test_rows} articoli tenuti fuori dall'addestramento e successivi nel tempo.
      </p>
    )
  }

  return null
}

function Confusion({ model }: { model: RiskModelMetrics }) {
  const cells = [
    { k: 'Segnalati e buttati', v: model.true_positives, good: true },
    { k: 'Segnalati per niente', v: model.false_positives, good: false },
    { k: 'Buttati senza preavviso', v: model.false_negatives, good: false },
    { k: 'Non segnalati e consumati', v: model.true_negatives, good: true },
  ]

  return (
    <div className="confusion">
      <div className="confusion-title">{model.name}</div>
      <div className="confusion-grid">
        {cells.map((c) => (
          <div className={`confusion-cell ${c.good ? 'is-good' : ''}`} key={c.k}>
            <strong>{c.v}</strong>
            <span>{c.k}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

/**
 * `data.notes` is deliberately not rendered here. It carries fitting diagnostics — "CoxPH
 * did not converge", "model written to /srv/data/risk", "constant in the training period,
 * dropped: …" — which belong in `make risk-fit`'s output and the eval notebook, not in a
 * caption on a household dashboard. The one note a user needs, the gate status, is already
 * on screen as two progress bars and a sentence.
 */
function Caption({ data }: { data: RiskEvaluation }) {
  const parts: string[] = []
  if (data.split_on) {
    parts.push(
      `Divisione temporale al ${data.split_on}: ${data.train_rows} articoli per addestrare, ${data.test_rows} per valutare. Mai casuale — un taglio a caso porterebbe consumi futuri dentro il passato.`,
    )
  }
  const evaluated = data.models[0]?.evaluated
  if (evaluated !== undefined) {
    parts.push(
      `Precision e recall sono misurate su ${evaluated} articoli risolti: numeri piccoli, da leggere come indicazioni.`,
    )
  }
  return <>{parts.join(' ')}</>
}

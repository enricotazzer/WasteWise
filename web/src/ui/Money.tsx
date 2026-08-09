import { Tile, type Tone } from './Tile'
import { coverageCaption, formatEur, type MoneyFigure } from './moneyFigure'

interface Props {
  figure: MoneyFigure
  label: React.ReactNode
  /** `tile` puts it in the KPI row; `inline` is for a sentence or a table cell. */
  variant?: 'tile' | 'inline'
  tone?: Tone
}

/**
 * A euro total, rendered with its price coverage or not at all.
 *
 * There is deliberately no prop to suppress the caption. The caption is what makes the
 * number honest, and a component that can be talked out of it would get talked out of it on
 * the first screen where the layout felt tight.
 */
export function Money({ figure, label, variant = 'inline', tone }: Props) {
  const value = formatEur(figure.eur)
  const caption = coverageCaption(figure)

  if (variant === 'tile') {
    return <Tile value={value} label={label} caption={caption} tone={tone} />
  }

  return (
    <span className="money">
      <span className="money-value">{value}</span>
      <span className="money-label">{label}</span>
      <span className="money-caption">{caption}</span>
    </span>
  )
}

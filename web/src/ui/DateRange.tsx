import { Segmented } from './Segmented'
import { RANGE_OPTIONS, type RangeKey } from './ranges'

interface Props {
  value: RangeKey
  onChange: (value: RangeKey) => void
  label?: string
}

/** The period switch. Pair it with `resolveRange` to turn the key into query params. */
export function DateRange({ value, onChange, label = 'Periodo' }: Props) {
  return <Segmented options={RANGE_OPTIONS} value={value} onChange={onChange} label={label} />
}

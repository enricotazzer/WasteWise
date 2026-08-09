/**
 * The chart layer. Like `ui/`, nothing in here knows what food is.
 *
 * Kept as its own folder rather than dropped into `ui/` because these components carry a
 * dependency the rest of the primitive layer doesn't have (Recharts) and a rule set the
 * rest doesn't need: fixed slot order, no dual axis, a table twin per chart. A bills module
 * gets all of it by importing from here.
 */
export { ChartFrame } from './ChartFrame'
export type { LegendItem, TableTwin } from './ChartFrame'
export { Readout } from './Readout'
export type { ReadoutRow } from './Readout'
export { Sparkline } from './Sparkline'
export { StackedBars } from './StackedBars'
export type { StackDatum, StackSeries } from './StackedBars'
export { TimeSeries } from './TimeSeries'
export type { LineSeries, TimeDatum } from './TimeSeries'
export { buildScale, OTHER_KEY, paletteFor, SERIES_CAP } from './palette'
export type { ColorScheme, SeriesScale } from './palette'
export { useChartTokens, useColorScheme, useSeriesPalette } from './useColorScheme'
export type { ChartTokens } from './useColorScheme'

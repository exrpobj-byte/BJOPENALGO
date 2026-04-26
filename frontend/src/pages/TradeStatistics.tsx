import {
  Activity,
  ArrowDownCircle,
  ArrowUpCircle,
  BarChart2,
  TrendingDown,
  TrendingUp,
  Zap,
} from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { cn, makeFormatCurrency } from '@/lib/utils'
import { statisticsApi, type StatisticsApiResponse } from '@/api/statistics'

const formatCurrency = makeFormatCurrency('INR')

function pctColor(value: number): string {
  return value >= 0 ? 'text-green-500' : 'text-red-500'
}

function valueColor(value: number): string {
  return value >= 0 ? 'text-green-500' : 'text-red-500'
}

function StatRow({
  label,
  value,
  className,
}: {
  label: string
  value: React.ReactNode
  className?: string
}) {
  return (
    <div className="flex items-center justify-between py-1.5 border-b border-border/40 last:border-0">
      <span className="text-sm text-muted-foreground">{label}</span>
      <span className={cn('text-sm font-medium', className)}>{value}</span>
    </div>
  )
}

export default function TradeStatistics() {
  const [data, setData] = useState<StatisticsApiResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Filters
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [strategy, setStrategy] = useState('')

  const fetchData = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await statisticsApi.getData(
        startDate || undefined,
        endDate || undefined,
        strategy || undefined
      )
      setData(result)
    } catch (err: unknown) {
      if (err instanceof Error) {
        setError(err.message)
      } else {
        setError('Failed to load statistics')
      }
    } finally {
      setLoading(false)
    }
  }, [startDate, endDate, strategy])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  const stats = data?.stats ?? null

  const streakBadge = (val: number) => {
    if (val > 0)
      return (
        <Badge className="bg-green-500/20 text-green-500 border-green-500/30">
          +{val} wins
        </Badge>
      )
    if (val < 0)
      return (
        <Badge className="bg-red-500/20 text-red-500 border-red-500/30">
          {val} losses
        </Badge>
      )
    return <Badge variant="outline">Neutral</Badge>
  }

  return (
    <div className="container mx-auto p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <BarChart2 className="h-6 w-6 text-primary" />
        <div>
          <h1 className="text-2xl font-bold">Trade Statistics</h1>
          <p className="text-sm text-muted-foreground">
            Paper trade performance metrics from Analyzer mode
          </p>
        </div>
      </div>

      {/* Filters */}
      <Card>
        <CardContent className="pt-4">
          <div className="flex flex-wrap gap-4 items-end">
            <div className="flex flex-col gap-1">
              <label className="text-xs text-muted-foreground">From</label>
              <input
                type="date"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
                className="border border-border rounded px-2 py-1.5 text-sm bg-background"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs text-muted-foreground">To</label>
              <input
                type="date"
                value={endDate}
                onChange={(e) => setEndDate(e.target.value)}
                className="border border-border rounded px-2 py-1.5 text-sm bg-background"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs text-muted-foreground">Strategy</label>
              <select
                value={strategy}
                onChange={(e) => setStrategy(e.target.value)}
                className="border border-border rounded px-2 py-1.5 text-sm bg-background min-w-[180px]"
              >
                <option value="">All Strategies</option>
                {data?.strategies?.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </div>
            <Button onClick={fetchData} disabled={loading} size="sm">
              {loading ? 'Loading…' : 'Apply'}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                setStartDate('')
                setEndDate('')
                setStrategy('')
              }}
            >
              Reset
            </Button>
            {data && (
              <span className="text-xs text-muted-foreground ml-auto self-end">
                {data.trade_count} round-trip trades
              </span>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Error */}
      {error && (
        <div className="rounded-md border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-500">
          {error}
        </div>
      )}

      {/* Stats Grid */}
      {stats && (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {/* Overall Performance */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-base">
                <Activity className="h-4 w-4 text-primary" />
                Overall Performance
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-0">
              <StatRow
                label="Total Trades"
                value={stats.overall.total_trades}
              />
              <StatRow
                label="Win Rate"
                value={
                  <span className={pctColor(stats.overall.win_rate - 50)}>
                    {stats.overall.win_rate.toFixed(1)}%
                  </span>
                }
              />
              <StatRow
                label="Wins"
                value={
                  <span className="text-green-500">{stats.overall.wins}</span>
                }
              />
              <StatRow
                label="Losses"
                value={
                  <span className="text-red-500">{stats.overall.losses}</span>
                }
              />
              <StatRow
                label="Profit Factor"
                value={
                  <span
                    className={
                      stats.overall.profit_factor >= 1
                        ? 'text-green-500'
                        : 'text-red-500'
                    }
                  >
                    {stats.overall.profit_factor.toFixed(2)}
                  </span>
                }
              />
            </CardContent>
          </Card>

          {/* Profit & Loss */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-base">
                <TrendingUp className="h-4 w-4 text-primary" />
                Profit & Loss
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-0">
              <StatRow
                label="Net P&L"
                value={formatCurrency(stats.pnl.net_pnl)}
                className={valueColor(stats.pnl.net_pnl)}
              />
              <StatRow
                label="Total Profit"
                value={formatCurrency(stats.pnl.total_profit)}
                className="text-green-500"
              />
              <StatRow
                label="Total Loss"
                value={formatCurrency(stats.pnl.total_loss)}
                className="text-red-500"
              />
              <StatRow
                label="Best Win"
                value={formatCurrency(stats.pnl.best_win)}
                className="text-green-500"
              />
              <StatRow
                label="Worst Loss"
                value={formatCurrency(stats.pnl.worst_loss)}
                className="text-red-500"
              />
              <StatRow
                label="Avg Win"
                value={formatCurrency(stats.pnl.avg_win)}
                className="text-green-500"
              />
              <StatRow
                label="Avg Loss"
                value={formatCurrency(stats.pnl.avg_loss)}
                className="text-red-500"
              />
            </CardContent>
          </Card>

          {/* Streaks */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-base">
                <Zap className="h-4 w-4 text-primary" />
                Streaks
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-0">
              <StatRow
                label="Best Win Streak"
                value={
                  <span className="text-green-500">
                    {stats.streaks.best_streak}
                  </span>
                }
              />
              <StatRow
                label="Worst Losing Run"
                value={
                  <span className="text-red-500">
                    {stats.streaks.worst_losing_run}
                  </span>
                }
              />
              <StatRow
                label="Current Streak"
                value={streakBadge(stats.streaks.current_streak)}
              />
            </CardContent>
          </Card>

          {/* BUY vs SELL */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-base">
                <ArrowUpCircle className="h-4 w-4 text-green-500" />
                BUY Trades
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-0">
              <StatRow
                label="Total BUY Trades"
                value={stats.direction.buy_trades}
              />
              <StatRow
                label="BUY Wins"
                value={
                  <span className="text-green-500">
                    {stats.direction.buy_wins}
                  </span>
                }
              />
              <StatRow
                label="BUY Win Rate"
                value={
                  <span className={pctColor(stats.direction.buy_win_rate - 50)}>
                    {stats.direction.buy_win_rate.toFixed(1)}%
                  </span>
                }
              />
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-base">
                <ArrowDownCircle className="h-4 w-4 text-red-500" />
                SELL Trades
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-0">
              <StatRow
                label="Total SELL Trades"
                value={stats.direction.sell_trades}
              />
              <StatRow
                label="SELL Wins"
                value={
                  <span className="text-green-500">
                    {stats.direction.sell_wins}
                  </span>
                }
              />
              <StatRow
                label="SELL Win Rate"
                value={
                  <span
                    className={pctColor(stats.direction.sell_win_rate - 50)}
                  >
                    {stats.direction.sell_win_rate.toFixed(1)}%
                  </span>
                }
              />
            </CardContent>
          </Card>

          {/* Manual Closes */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-base">
                <TrendingDown className="h-4 w-4 text-yellow-500" />
                Manual Closes
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-0">
              <StatRow
                label="Total Manual Closes"
                value={stats.manual_closes.total}
              />
              <StatRow
                label="Profitable"
                value={
                  <span className="text-green-500">
                    {stats.manual_closes.profit_count}
                  </span>
                }
              />
              <StatRow
                label="At Loss"
                value={
                  <span className="text-red-500">
                    {stats.manual_closes.loss_count}
                  </span>
                }
              />
              <StatRow
                label="Win Rate"
                value={
                  <span
                    className={pctColor(stats.manual_closes.win_rate - 50)}
                  >
                    {stats.manual_closes.win_rate.toFixed(1)}%
                  </span>
                }
              />
              <StatRow
                label="P&L from Manual"
                value={formatCurrency(stats.manual_closes.pnl)}
                className={valueColor(stats.manual_closes.pnl)}
              />
            </CardContent>
          </Card>
        </div>
      )}

      {/* Empty state */}
      {!loading && !error && stats && stats.overall.total_trades === 0 && (
        <div className="text-center py-12 text-muted-foreground">
          <BarChart2 className="h-12 w-12 mx-auto mb-3 opacity-30" />
          <p>No trades found for the selected filters.</p>
          <p className="text-xs mt-1">
            Run strategies in Analyzer mode to see statistics here.
          </p>
        </div>
      )}
    </div>
  )
}

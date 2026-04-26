// frontend/src/api/statistics.ts
import { webClient } from './client'

export interface OverallStats {
  total_trades: number
  win_rate: number
  wins: number
  losses: number
  profit_factor: number
}

export interface PnLStats {
  total_profit: number
  total_loss: number
  best_win: number
  worst_loss: number
  avg_win: number
  avg_loss: number
  net_pnl: number
}

export interface StreakStats {
  best_streak: number
  worst_losing_run: number
  current_streak: number
}

export interface DirectionStats {
  buy_trades: number
  buy_wins: number
  buy_win_rate: number
  sell_trades: number
  sell_wins: number
  sell_win_rate: number
}

export interface ManualClosesStats {
  total: number
  profit_count: number
  loss_count: number
  win_rate: number
  pnl: number
}

export interface TradeStatistics {
  overall: OverallStats
  pnl: PnLStats
  streaks: StreakStats
  direction: DirectionStats
  manual_closes: ManualClosesStats
}

export interface StatisticsFilters {
  start_date?: string | null
  end_date?: string | null
  strategy?: string | null
}

export interface StatisticsApiResponse {
  status: 'success' | 'error'
  stats: TradeStatistics
  trade_count: number
  strategies: string[]
  filters: StatisticsFilters
  message?: string
}

export const statisticsApi = {
  getData: async (
    startDate?: string,
    endDate?: string,
    strategy?: string
  ): Promise<StatisticsApiResponse> => {
    const params = new URLSearchParams()
    if (startDate) params.set('start_date', startDate)
    if (endDate) params.set('end_date', endDate)
    if (strategy) params.set('strategy', strategy)

    const url = `/trade-statistics/api/data${params.toString() ? `?${params.toString()}` : ''}`
    const response = await webClient.get<StatisticsApiResponse>(url)
    return response.data
  },
}

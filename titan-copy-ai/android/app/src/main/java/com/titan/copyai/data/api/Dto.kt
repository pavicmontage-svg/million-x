package com.titan.copyai.data.api

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject

@Serializable data class PairRequest(val code: String, @SerialName("device_name") val deviceName: String)
@Serializable data class PairResponse(val token: String)

@Serializable
data class PortfolioSummary(
    val id: Int,
    val name: String,
    val market: String,
    val venue: String,
    val mode: String,
    val simulated: Boolean = false,
    val currency: String,
    val status: String,
    @SerialName("status_reason") val statusReason: String? = null,
    @SerialName("copy_enabled") val copyEnabled: Boolean = true,
    val balance: Double? = null,
    val equity: Double? = null,
    @SerialName("available_margin") val availableMargin: Double? = null,
    @SerialName("daily_pnl") val dailyPnl: Double? = null,
    @SerialName("monthly_pnl") val monthlyPnl: Double? = null,
    @SerialName("total_pnl") val totalPnl: Double? = null,
    @SerialName("drawdown_pct") val drawdownPct: Double? = null,
    @SerialName("open_risk") val openRisk: Double? = null,
    @SerialName("daily_risk_usage_pct") val dailyRiskUsagePct: Double? = null,
    @SerialName("active_traders") val activeTraders: Int = 0,
    @SerialName("open_positions") val openPositions: Int = 0,
    @SerialName("initial_capital") val initialCapital: Double = 0.0,
    @SerialName("snapshot_at") val snapshotAt: String? = null,
)

@Serializable
data class Dashboard(
    val portfolios: List<PortfolioSummary> = emptyList(),
    @SerialName("copy_engine") val copyEngine: JsonObject = JsonObject(emptyMap()),
    @SerialName("kill_switch") val killSwitch: JsonObject = JsonObject(emptyMap()),
    @SerialName("market_status") val marketStatus: JsonObject = JsonObject(emptyMap()),
    @SerialName("ai_risk_assessment") val aiRiskAssessment: String? = null,
    @SerialName("ai_risk_summary") val aiRiskSummary: String? = null,
    @SerialName("unread_notifications") val unreadNotifications: Int = 0,
)

@Serializable
data class EquityPoint(val t: String, val equity: Double, val balance: Double, val dd: Double = 0.0)

@Serializable
data class TraderRow(
    val id: Int,
    val name: String,
    val platform: String,
    val market: String,
    val status: String,
    @SerialName("copy_capability") val copyCapability: String,
    val category: String? = null,
    @SerialName("data_status") val dataStatus: String,
    val score: Double? = null,
    val rejected: Boolean = false,
    val eligible: Boolean = false,
    @SerialName("roi_30d") val roi30d: Double? = null,
    @SerialName("roi_365d") val roi365d: Double? = null,
    @SerialName("max_drawdown_pct") val maxDrawdownPct: Double? = null,
    @SerialName("profit_factor") val profitFactor: Double? = null,
    @SerialName("win_rate") val winRate: Double? = null,
    @SerialName("verified_at") val verifiedAt: String? = null,
)

@Serializable
data class TraderDetail(
    val id: Int,
    val name: String,
    val platform: String,
    val broker: String? = null,
    @SerialName("trader_id") val externalId: String? = null,
    @SerialName("account_type") val accountType: String? = null,
    val market: String,
    @SerialName("strategy_description") val strategyDescription: String? = null,
    val instruments: List<String> = emptyList(),
    val status: String,
    @SerialName("copy_capability") val copyCapability: String,
    val category: String? = null,
    @SerialName("data_status") val dataStatus: String,
    @SerialName("data_note") val dataNote: String? = null,
    @SerialName("source_url") val sourceUrl: String? = null,
    @SerialName("data_source") val dataSource: String? = null,
    @SerialName("data_verification_timestamp") val verifiedTs: String? = null,
    val metrics: Map<String, JsonElement?> = emptyMap(),
    val score: Double? = null,
    val rejected: Boolean = false,
    val components: JsonObject = JsonObject(emptyMap()),
    val rejections: List<JsonObject> = emptyList(),
    val flags: List<JsonObject> = emptyList(),
    val categories: List<String> = emptyList(),
    val explanation: String = "",
    @SerialName("score_history") val scoreHistory: List<JsonObject> = emptyList(),
    @SerialName("equity_curve") val equityCurve: List<JsonObject> = emptyList(),
    @SerialName("recent_trades") val recentTrades: List<JsonObject> = emptyList(),
    val allocations: List<JsonObject> = emptyList(),
    @SerialName("signal_source_configured") val signalSourceConfigured: Boolean = false,
)

@Serializable
data class PositionDto(
    val id: Long,
    @SerialName("portfolio_id") val portfolioId: Int,
    val trader: String? = null,
    val symbol: String,
    val side: String,
    val quantity: Double,
    @SerialName("initial_quantity") val initialQuantity: Double = 0.0,
    @SerialName("entry_price") val entryPrice: Double,
    val sl: Double? = null,
    val tp: Double? = null,
    @SerialName("initial_risk") val initialRisk: Double = 0.0,
    val status: String,
    @SerialName("opened_at") val openedAt: String,
    @SerialName("closed_at") val closedAt: String? = null,
    @SerialName("exit_price") val exitPrice: Double? = null,
    @SerialName("realized_pnl") val realizedPnl: Double = 0.0,
    val fees: Double = 0.0,
    @SerialName("slippage_cost") val slippageCost: Double = 0.0,
    @SerialName("r_multiple") val rMultiple: Double? = null,
    @SerialName("close_reason") val closeReason: String? = null,
    @SerialName("copy_delay_ms") val copyDelayMs: Double? = null,
    @SerialName("liquidation_price") val liquidationPrice: Double? = null,
    val simulated: Boolean = false,
)

@Serializable
data class AllocationDto(
    val id: Int,
    @SerialName("portfolio_id") val portfolioId: Int,
    @SerialName("trader_id") val traderId: Int,
    val enabled: Boolean,
    @SerialName("capital_fraction") val capitalFraction: Double,
    @SerialName("max_risk_pct") val maxRiskPct: Double,
    @SerialName("stopped_reason") val stoppedReason: String? = null,
    @SerialName("trader_name") val traderName: String,
)

@Serializable
data class AllocationIn(
    @SerialName("portfolio_id") val portfolioId: Int,
    @SerialName("trader_id") val traderId: Int,
    val enabled: Boolean = true,
    @SerialName("capital_fraction") val capitalFraction: Double = 1.0,
    @SerialName("max_risk_pct") val maxRiskPct: Double = 0.5,
)

@Serializable
data class AgentDto(
    val name: String,
    val title: String,
    val version: String,
    val prompt: String,
    @SerialName("prompt_versions") val promptVersions: List<JsonObject> = emptyList(),
    val metrics: JsonObject = JsonObject(emptyMap()),
    @SerialName("last_run") val lastRun: JsonObject? = null,
)

@Serializable
data class NotificationDto(
    val id: Long,
    val ts: String,
    val kind: String,
    val severity: String,
    val title: String,
    val body: String,
    val read: Boolean = false,
)

@Serializable
data class ConnectionDto(
    val id: Int,
    val venue: String,
    val environment: String,
    val label: String,
    val fingerprint: String,
    @SerialName("created_at") val createdAt: String,
    @SerialName("verified_at") val verifiedAt: String? = null,
    @SerialName("verify_result") val verifyResult: JsonObject = JsonObject(emptyMap()),
    val portfolios: List<String> = emptyList(),
)

@Serializable
data class CredentialIn(val venue: String, val environment: String, val label: String, val fields: Map<String, String>)

@Serializable
data class AuditDto(val id: Long, val ts: String, val actor: String, val action: String, val entity: String? = null,
                    val detail: JsonObject = JsonObject(emptyMap()), val hash: String)

@Serializable data class SignalDto(
    val id: Long,
    @SerialName("trader_id") val traderId: Int? = null,
    val kind: String,
    val symbol: String,
    val side: String? = null,
    val entry: Double? = null,
    val sl: Double? = null,
    val tp: Double? = null,
    val status: String,
    val outcome: List<JsonObject> = emptyList(),
    @SerialName("received_at") val receivedAt: String,
    val simulated: Boolean = false,
)

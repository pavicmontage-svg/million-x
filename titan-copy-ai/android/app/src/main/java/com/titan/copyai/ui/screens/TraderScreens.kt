package com.titan.copyai.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.titan.copyai.data.api.TraderRow
import com.titan.copyai.ui.components.ChartPoint
import com.titan.copyai.ui.components.Chip
import com.titan.copyai.ui.components.Divider
import com.titan.copyai.ui.components.Empty
import com.titan.copyai.ui.components.Fmt
import com.titan.copyai.ui.components.KV
import com.titan.copyai.ui.components.LineChart
import com.titan.copyai.ui.components.Metric
import com.titan.copyai.ui.components.Panel
import com.titan.copyai.ui.components.ScoreBar
import com.titan.copyai.ui.components.UNKNOWN
import com.titan.copyai.ui.components.at
import com.titan.copyai.ui.components.d
import com.titan.copyai.ui.components.metricText
import com.titan.copyai.ui.components.obj
import com.titan.copyai.ui.components.pnlColor
import com.titan.copyai.ui.components.s
import com.titan.copyai.ui.components.statusColor
import com.titan.copyai.ui.nav.LocalCtx
import com.titan.copyai.ui.theme.Titan
import com.titan.copyai.ui.vm.live
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

private val METRIC_ORDER = listOf(
    "account_age_days", "roi_7d", "roi_30d", "roi_90d", "roi_180d", "roi_365d", "max_equity_drawdown_pct",
    "max_balance_drawdown_pct", "win_rate", "profit_factor", "avg_risk_reward", "avg_trade_duration_h",
    "trades_per_day", "avg_win", "avg_loss", "max_consecutive_losses", "max_leverage", "open_exposure",
    "current_positions", "trading_fees", "slippage", "active_copiers", "last_activity", "total_trades",
    "expectancy", "sharpe", "sortino", "calmar", "recovery_factor", "monthly_consistency", "return_volatility_ann",
    "exposure_concentration", "sl_usage_rate", "net_profit", "months_of_history",
)

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun MarketplaceScreen() {
    val ctx = LocalCtx.current
    var market by remember { mutableStateOf<String?>(null) }
    val vm = rememberLoad("traders-${market ?: "all"}") { traders(market) }
    LoadedList(vm) { list ->
        item {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf(null to "All", "forex" to "Forex", "crypto" to "Crypto").forEach { (m, l) ->
                    FilterChip(selected = market == m, onClick = { market = m }, label = { Text(l) })
                }
            }
        }
        item {
            Text("Ranking is risk-adjusted (drawdown 25%, risk-adjusted return 20%, profit factor 15%, consistency 15%, " +
                "history 10%, execution 10%, transparency 5%). Candidates are not recommendations; past performance " +
                "does not guarantee future results.", style = MaterialTheme.typography.bodySmall, color = Titan.Muted)
        }
        if (list.isEmpty()) item { Empty("No traders yet") }
        items(list, key = { it.id }) { t -> TraderCard(t) { ctx.nav.navigate("trader/${t.id}") } }
    }
}

@Composable
private fun TraderCard(t: TraderRow, onClick: () -> Unit) {
    Panel(onClick = onClick) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(t.name, style = MaterialTheme.typography.titleMedium)
                Text("${t.platform} · ${t.market} · ${t.category ?: "uncategorised"}", color = Titan.Muted,
                    style = MaterialTheme.typography.bodySmall)
            }
            Column(horizontalAlignment = Alignment.End) {
                Text(t.score?.let { Fmt.num(it, 0) } ?: "—", style = MaterialTheme.typography.headlineMedium,
                    color = when { t.rejected -> Titan.Down; (t.score ?: 0.0) >= 70 -> Titan.Up; t.score == null -> Titan.Muted
                        else -> Titan.Warn })
                Text(if (t.score == null) "NOT SCORED" else "SCORE", style = MaterialTheme.typography.labelSmall, color = Titan.Muted)
            }
        }
        Spacer(Modifier.height(8.dp))
        ScoreBar(t.score)
        Spacer(Modifier.height(8.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Metric("ROI 30D", Fmt.signedPct(t.roi30d), Modifier.weight(1f), pnlColor(t.roi30d))
            Metric("ROI 365D", Fmt.signedPct(t.roi365d), Modifier.weight(1f), pnlColor(t.roi365d))
            Metric("Max DD", Fmt.pct(t.maxDrawdownPct), Modifier.weight(1f))
            Metric("PF", Fmt.num(t.profitFactor), Modifier.weight(1f))
        }
        Spacer(Modifier.height(8.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Chip(t.status, statusColor(t.status))
            if (t.rejected) Chip("rejected", Titan.Down)
            if (t.eligible) Chip("eligible", Titan.Up)
            Chip(t.copyCapability.replace('_', ' '), if (t.copyCapability == "authorized_signals") Titan.Accent else Titan.Muted)
        }
    }
}

@Composable
fun TraderDetailScreen(id: Int) {
    val ctx = LocalCtx.current
    val vm = rememberLoad("trader-$id") { live(api.trader(id)) }
    var secret by remember { mutableStateOf<String?>(null) }
    LoadedList(vm) { t ->
        item {
            Panel(title = "${t.platform} · ${t.market}", trailing = { Chip(t.status, statusColor(t.status)) }) {
                Text(t.name, style = MaterialTheme.typography.headlineMedium)
                KV("Broker", t.broker ?: UNKNOWN)
                KV("Trader ID", t.externalId ?: UNKNOWN)
                KV("Account type", t.accountType ?: UNKNOWN)
                KV("Instruments", t.instruments.joinToString().ifBlank { UNKNOWN })
                KV("Copy capability", t.copyCapability.replace('_', ' '))
                KV("Data source", t.dataSource ?: UNKNOWN)
                KV("Data verified", Fmt.time(t.verifiedTs))
                if (!t.strategyDescription.isNullOrBlank()) Text(t.strategyDescription, style = MaterialTheme.typography.bodySmall)
                if (!t.dataNote.isNullOrBlank()) Text(t.dataNote, style = MaterialTheme.typography.bodySmall, color = Titan.Warn,
                    modifier = Modifier.padding(top = 6.dp))
            }
        }
        item {
            Panel(title = "Score & explanation") {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(t.score?.let { Fmt.num(it, 1) } ?: "NOT SCORABLE", style = MaterialTheme.typography.headlineMedium,
                        color = if (t.rejected) Titan.Down else Titan.Text)
                    Spacer(Modifier.width(10.dp))
                    t.categories.forEach { Chip(it, Titan.Gold); Spacer(Modifier.width(4.dp)) }
                }
                Spacer(Modifier.height(6.dp))
                Text(t.explanation, style = MaterialTheme.typography.bodyMedium)
                Divider()
                t.components.entries.filter { !it.key.startsWith("_") }.forEach { (k, v) ->
                    val o = v.obj()
                    Text("${Fmt.label(k)} · weight ${o.at("weight").s()}%", style = MaterialTheme.typography.labelMedium)
                    ScoreBar(o.at("score").d())
                    Text(o.at("why").s() ?: "", style = MaterialTheme.typography.bodySmall, color = Titan.Muted,
                        modifier = Modifier.padding(bottom = 6.dp))
                }
                t.rejections.forEach { Text("✕ " + (it.at("message").s() ?: ""), color = Titan.Down, style = MaterialTheme.typography.bodySmall) }
                t.flags.forEach { Text("! " + (it.at("message").s() ?: ""), color = Titan.Warn, style = MaterialTheme.typography.bodySmall) }
            }
        }
        item {
            Panel(title = "Historical equity curve") {
                val pts = t.equityCurve.mapNotNull { p ->
                    val y = p.at("equity").d() ?: p.at("balance").d() ?: p.at("growth_pct").d()?.let { 100 * (1 + it / 100) }
                    y?.let { ChartPoint(Fmt.epochMillis(p.at("t").s() ?: ""), it, Fmt.time(p.at("t").s())) }
                }
                LineChart(pts)
            }
        }
        item {
            Panel(title = "All metrics (UNKNOWN = not published by the source)") {
                METRIC_ORDER.forEach { k -> KV(Fmt.label(k), metricText(k, t.metrics[k])) }
            }
        }
        item {
            Panel(title = "Recent trades") {
                if (t.recentTrades.isEmpty()) Empty("No trade-level history")
                t.recentTrades.take(25).forEach { tr ->
                    val pnl = tr.at("profit").d()
                    KV("${tr.at("side").s()?.uppercase()} ${tr.at("symbol").s()} ${Fmt.time(tr.at("open_time").s())}",
                        Fmt.signed(pnl), pnlColor(pnl))
                }
            }
        }
        item {
            Panel(title = "Actions") {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button({ vm.action("Approved") { ctx.app.repo.api.traderStatus(id, buildJsonObject { put("status", "approved") }) } },
                        enabled = !t.rejected && t.score != null) { Text("Approve") }
                    OutlinedButton({ vm.action("Suspended") { ctx.app.repo.api.traderStatus(id, buildJsonObject { put("status", "suspended") }) } }) {
                        Text("Suspend")
                    }
                    OutlinedButton({ ctx.nav.navigate("analytics/$id") }) { Text("Analytics") }
                }
                Spacer(Modifier.height(8.dp))
                OutlinedButton({
                    vm.action("Signal source created", refreshAfter = true) {
                        secret = ctx.app.repo.api.signalSource(id)["secret"]?.let { (it as JsonPrimitive).content }
                    }
                }) { Text(if (t.signalSourceConfigured) "Rotate signal secret" else "Create authorised signal source") }
                secret?.let {
                    Text("Webhook secret (shown once - give it only to the authorised provider):", color = Titan.Gold,
                        style = MaterialTheme.typography.bodySmall)
                    Text(it, style = MaterialTheme.typography.bodySmall)
                }
                Text("A public track record does not make an account copyable. Copying requires an authorised source " +
                    "(your own account or a provider that signs signals).", color = Titan.Muted, style = MaterialTheme.typography.bodySmall)
                SourceAccountPicker(id, t.market, t.sourceAccount, vm)
            }
        }
    }
}

@Composable
fun AnalyticsScreen(traderId: Int?) {
    val ctx = LocalCtx.current
    if (traderId == null) {
        val vm = rememberLoad("traders-analytics") { traders(null) }
        LoadedList(vm) { list ->
            item { Text("Choose a trader with imported or retrieved history", color = Titan.Muted) }
            items(list.filter { it.score != null || it.dataStatus != "unverified" }, key = { it.id }) { t ->
                Panel(onClick = { ctx.nav.navigate("analytics/${t.id}") }) {
                    Text(t.name, style = MaterialTheme.typography.titleMedium)
                    Text("${t.platform} · score ${t.score?.let { Fmt.num(it, 0) } ?: "—"}", color = Titan.Muted)
                }
            }
        }
        return
    }
    val vm = rememberLoad("analytics-$traderId") { live(api.analytics(traderId)) }
    val bt = rememberLoad("backtest-$traderId") {
        live(api.backtest(buildJsonObject {
            put("trader_id", traderId); put("delay_ms", 1500); put("failure_prob", 0.02); put("disconnect_prob", 0.01)
            put("partial_fill_prob", 0.05)
        }))
    }
    LoadedList(vm) { a ->
        item {
            Panel(title = "Expectancy") {
                val e = a.at("expectancy").obj()
                KV("Trades", a.at("trades").s() ?: UNKNOWN)
                KV("Expectancy / trade", Fmt.num(e.at("expectancy").d()))
                KV("Win rate", Fmt.pct(e.at("win_rate").d()))
                KV("Profit factor", Fmt.num(e.at("profit_factor").d()))
                KV("t-statistic", Fmt.num(e.at("t_stat").d()))
                KV("Edge significant (95%)", e.at("significant_95").s() ?: UNKNOWN)
                KV("Return basis", a.at("return_basis").s() ?: UNKNOWN)
            }
        }
        item {
            Panel(title = "Monte Carlo (bootstrap of the trader's own trade returns)") {
                val mc = a.at("monte_carlo").obj()
                if (mc.at("error") != null) Text(mc.at("error").s() ?: "", color = Titan.Muted) else {
                    KV("Simulations", mc.at("simulations").s() ?: "")
                    KV("Final return p5 / median / p95", "${Fmt.pct(mc.at("final_return_pct", "p5").d())} / " +
                        "${Fmt.pct(mc.at("final_return_pct", "median").d())} / ${Fmt.pct(mc.at("final_return_pct", "p95").d())}")
                    KV("Max DD median / p95 / p99", "${Fmt.pct(mc.at("max_drawdown_pct", "median").d())} / " +
                        "${Fmt.pct(mc.at("max_drawdown_pct", "p95").d())} / ${Fmt.pct(mc.at("max_drawdown_pct", "p99").d())}")
                    KV("P(loss)", Fmt.pct((mc.at("prob_loss").d() ?: 0.0) * 100))
                    KV("P(DD ≥ 10%)", Fmt.pct((mc.at("prob_drawdown_exceeds", "probability").d() ?: 0.0) * 100))
                }
            }
        }
        item {
            Panel(title = "Walk-forward stability") {
                val wf = a.at("walk_forward").obj()
                if (wf.at("error") != null) Text(wf.at("error").s() ?: "", color = Titan.Muted) else {
                    KV("Out-of-sample positive windows", Fmt.pct((wf.at("oos_positive_share").d() ?: 0.0) * 100, 0))
                    KV("Walk-forward efficiency", Fmt.num(wf.at("walk_forward_efficiency").d()))
                    KV("Stable", wf.at("stable").s() ?: UNKNOWN)
                }
            }
        }
        item { BacktestPanel(bt) }
    }
}

@Composable
private fun BacktestPanel(bt: com.titan.copyai.ui.vm.LoadVm<kotlinx.serialization.json.JsonObject>) {
    val st = bt.collectAsStateValue()
    Panel(title = "Copy backtest (SIMULATED: 500 capital, 0.5% risk, delay/spread/fees/failures)") {
        when (st) {
            is com.titan.copyai.ui.vm.UiState.Data -> {
                val r = st.value
                val m = r.at("metrics").obj()
                val s = r.at("stats").obj()
                KV("Net profit", Fmt.signed(m.at("net_profit").d()), pnlColor(m.at("net_profit").d()))
                KV("Profit factor", Fmt.num(m.at("profit_factor").d()))
                KV("Max drawdown", Fmt.pct(m.at("max_drawdown_pct").d()))
                KV("Sharpe / Sortino", "${Fmt.num(m.at("sharpe_per_trade_ann").d())} / ${Fmt.num(m.at("sortino_per_trade_ann").d())}")
                KV("Win rate", Fmt.pct(m.at("win_rate").d()))
                KV("Avg R", Fmt.num(m.at("avg_r_multiple").d(), 3))
                KV("Execution accuracy", Fmt.pct((m.at("execution_accuracy").d() ?: 0.0) * 100))
                KV("Executed / skipped (no SL)", "${s.at("executed").s()} / ${s.at("skipped_no_stop_loss").s()}")
                KV("Cost model", r.at("cost_model").s() ?: UNKNOWN)
                val pts = (r.at("equity_curve") as? kotlinx.serialization.json.JsonArray)?.mapNotNull { e ->
                    val o = e.obj(); o.at("equity").d()?.let { ChartPoint(0, it, Fmt.time(o.at("t").s())) }
                } ?: emptyList()
                LineChart(pts, baseline = 500.0)
            }
            is com.titan.copyai.ui.vm.UiState.Error -> Text(st.message, color = Titan.Down)
            else -> Text("Running backtest…", color = Titan.Muted)
        }
    }
}

@Composable
private fun <T> com.titan.copyai.ui.vm.LoadVm<T>.collectAsStateValue() = state.collectAsState().value


/** Wire one of the owner's connected accounts as this trader's signal source (polled by the engine). */
@Composable
private fun SourceAccountPicker(traderId: Int, market: String, current: kotlinx.serialization.json.JsonObject?,
                                vm: com.titan.copyai.ui.vm.LoadVm<com.titan.copyai.data.api.TraderDetail>) {
    val ctx = LocalCtx.current
    var accounts by remember { mutableStateOf<List<com.titan.copyai.data.api.ConnectionDto>>(emptyList()) }
    androidx.compose.runtime.LaunchedEffect(traderId) {
        accounts = runCatching { ctx.app.repo.api.connections() }.getOrDefault(emptyList()).filter {
            (market == "crypto" && it.venue in setOf("binance_usdm", "binance_coinm")) ||
                (market == "forex" && it.venue in setOf("mt5", "ctrader"))
        }
    }
    Spacer(Modifier.height(8.dp))
    Text("SOURCE ACCOUNT (no webhook needed)", style = MaterialTheme.typography.labelSmall, color = Titan.Muted)
    if (current != null) {
        KV("Polling", "${current.at("type").s()} · ${current.at("environment").s()} · since ${Fmt.time(current.at("since").s())}", Titan.Accent)
        OutlinedButton({ vm.action("Source removed") { ctx.app.repo.api.removeSourceAccount(traderId) } }) { Text("Stop using this account") }
    } else if (accounts.isEmpty()) {
        Text("Add a ${if (market == "crypto") "Binance futures" else "MT5 or cTrader"} connection first; its new positions " +
            "will be copied (positions already open are not).", color = Titan.Muted, style = MaterialTheme.typography.bodySmall)
    }
    if (current == null) accounts.forEach { a ->
        OutlinedButton({
            vm.action("Now copying from ${a.label}") {
                ctx.app.repo.api.sourceAccount(traderId, buildJsonObject { put("credential_id", a.id); put("environment", a.environment) })
            }
        }) { Text("Copy from: ${a.label} (${a.venue}, ${a.environment})") }
    }
}

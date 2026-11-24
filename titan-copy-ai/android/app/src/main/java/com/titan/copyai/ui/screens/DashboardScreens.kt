package com.titan.copyai.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.titan.copyai.data.api.Dashboard
import com.titan.copyai.data.api.PortfolioSummary
import com.titan.copyai.ui.components.ChartPoint
import com.titan.copyai.ui.components.Chip
import com.titan.copyai.ui.components.Empty
import com.titan.copyai.ui.components.Fmt
import com.titan.copyai.ui.components.KV
import com.titan.copyai.ui.components.LineChart
import com.titan.copyai.ui.components.Metric
import com.titan.copyai.ui.components.Panel
import com.titan.copyai.ui.components.SimulatedBadge
import com.titan.copyai.ui.components.StatusDot
import com.titan.copyai.ui.components.UsageBar
import com.titan.copyai.ui.components.at
import com.titan.copyai.ui.components.b
import com.titan.copyai.ui.components.d
import com.titan.copyai.ui.components.pnlColor
import com.titan.copyai.ui.components.s
import com.titan.copyai.ui.components.statusColor
import com.titan.copyai.ui.nav.LocalCtx
import com.titan.copyai.ui.theme.Titan
import com.titan.copyai.ui.vm.AppVm
import com.titan.copyai.ui.vm.LoadVm
import com.titan.copyai.ui.vm.UiState
import com.titan.copyai.ui.vm.live

@Composable
fun PairingScreen(app: AppVm) {
    var url by remember { mutableStateOf("https://") }
    var code by remember { mutableStateOf("") }
    var remote by remember { mutableStateOf(false) }
    val err by app.pairError.collectAsState()
    val status by app.localStatus.collectAsState()
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(24.dp),
        verticalArrangement = Arrangement.Center) {
        Text("TITAN COPY AI", style = MaterialTheme.typography.headlineLarge, color = Titan.Gold)
        Text("Copy-trading engine that runs on this phone", color = Titan.Muted)
        Spacer(Modifier.height(28.dp))
        Panel(title = "Run on this phone (recommended)") {
            Text("The copy engine, risk limits, AI agents and the encrypted credential vault run here, 24/7, in a " +
                "background service. No server needed. Starts in paper mode: Forex €500 and Crypto 500 USDT (SIMULATED).",
                style = MaterialTheme.typography.bodyMedium, color = Titan.Muted)
            Spacer(Modifier.height(12.dp))
            Button({ app.startLocal() }, enabled = status == null, modifier = Modifier.fillMaxWidth()) {
                Text(if (status == null) "Start engine on this phone" else "Starting…")
            }
            status?.let { Text(it, color = Titan.Accent, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(top = 6.dp)) }
            Text("First start unpacks the embedded Python engine (up to ~1 minute).", color = Titan.Muted,
                style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(top = 6.dp))
        }
        Spacer(Modifier.height(16.dp))
        androidx.compose.material3.TextButton({ remote = !remote }) {
            Text(if (remote) "Hide server option" else "Advanced: connect to a remote TITAN server instead")
        }
        if (remote) {
            Panel(title = "Remote server") {
                OutlinedTextField(url, { url = it }, label = { Text("Server URL (https://…)") }, singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri), modifier = Modifier.fillMaxWidth())
                Spacer(Modifier.height(8.dp))
                OutlinedTextField(code, { code = it.uppercase() }, label = { Text("Pairing code") }, singleLine = true,
                    modifier = Modifier.fillMaxWidth())
                Spacer(Modifier.height(12.dp))
                Button({ app.pair(url, code) }, enabled = url.length > 10 && code.length >= 6, modifier = Modifier.fillMaxWidth()) {
                    Text("Pair device")
                }
            }
        }
        err?.let { Text(it, color = Titan.Down, modifier = Modifier.padding(top = 8.dp)) }
    }
}

@Composable
fun dashboardVm(): LoadVm<Dashboard> = rememberLoad("dashboard") { dashboard() }

@Composable
fun DashboardScreen() {
    val ctx = LocalCtx.current
    val vm = dashboardVm()
    LoadedList(vm) { d ->
        item { SystemStrip(d) }
        val total = d.portfolios
        items(total, key = { it.id }) { p -> PortfolioCard(p) { ctx.nav.navigate(if (p.market == "forex") "forex" else "binance") } }
        item {
            Panel(title = "AI risk assessment", onClick = { ctx.nav.navigate("ai") }) {
                val a = d.aiRiskAssessment
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Chip(a ?: "not assessed", when (a) { "low_risk" -> Titan.Up; "moderate_risk" -> Titan.Warn
                        "high_risk" -> Titan.Down; else -> Titan.Muted })
                }
                Spacer(Modifier.height(6.dp))
                Text(d.aiRiskSummary ?: "No LLM review yet. Deterministic risk controls are always active.",
                    style = MaterialTheme.typography.bodyMedium, color = Titan.Muted)
            }
        }
    }
}

@Composable
fun SystemStrip(d: Dashboard) {
    val running = d.copyEngine.at("running").b() == true
    val kill = d.killSwitch.at("active").b() == true
    Panel(title = "System status") {
        Row(horizontalArrangement = Arrangement.spacedBy(16.dp)) {
            StatusDot(running, if (running) "Copy engine running" else "Copy engine DOWN")
            StatusDot(!kill, if (kill) "EMERGENCY STOP" else "Trading allowed")
        }
        Spacer(Modifier.height(8.dp))
        val mins = d.marketStatus.at("minutes_to_rollover").d()
        val weekend = d.marketStatus.at("forex_weekend").b() == true
        Text(
            (if (weekend) "Forex market closed (weekend). " else "Forex market open. ") +
                "Rollover in ${Fmt.num(mins, 0)} min (new forex entries blocked 15 min before).",
            style = MaterialTheme.typography.bodySmall, color = Titan.Muted)
        if (kill) Text("Reason: ${d.killSwitch.at("reason").s()}", color = Titan.Down, style = MaterialTheme.typography.bodySmall)
    }
}

@Composable
fun PortfolioCard(p: PortfolioSummary, onClick: () -> Unit) {
    Panel(onClick = onClick, title = "${p.name} · ${p.venue} · ${p.mode}", trailing = {
        SimulatedBadge(p.simulated); Spacer(Modifier.width(6.dp)); Chip(p.status, statusColor(p.status))
    }) {
        Text(Fmt.money(p.equity, p.currency), style = MaterialTheme.typography.headlineMedium)
        Text("Equity  ·  balance ${Fmt.money(p.balance, p.currency)}", color = Titan.Muted, style = MaterialTheme.typography.bodySmall)
        Spacer(Modifier.height(10.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Metric("Daily P&L", Fmt.signed(p.dailyPnl), Modifier.weight(1f), pnlColor(p.dailyPnl))
            Metric("Monthly P&L", Fmt.signed(p.monthlyPnl), Modifier.weight(1f), pnlColor(p.monthlyPnl))
            Metric("Total P&L", Fmt.signed(p.totalPnl), Modifier.weight(1f), pnlColor(p.totalPnl))
        }
        Spacer(Modifier.height(8.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Metric("Free margin", Fmt.num(p.availableMargin), Modifier.weight(1f))
            Metric("Drawdown", Fmt.pct(p.drawdownPct), Modifier.weight(1f),
                if ((p.drawdownPct ?: 0.0) > 5) Titan.Warn else Titan.Text)
            Metric("Traders / Pos.", "${p.activeTraders} / ${p.openPositions}", Modifier.weight(1f))
        }
        Spacer(Modifier.height(10.dp))
        UsageBar("Daily loss-limit usage", p.dailyRiskUsagePct)
        if (!p.statusReason.isNullOrBlank()) Text(p.statusReason, color = Titan.Warn, style = MaterialTheme.typography.bodySmall,
            modifier = Modifier.padding(top = 6.dp))
        if (!p.copyEnabled) {
            val ctx = LocalCtx.current
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 4.dp)) {
                Text("Automatic copying is OFF", color = Titan.Down, style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.weight(1f))
                androidx.compose.material3.TextButton({ ctx.nav.navigate("portfolio") }) { Text("Set up copying") }
            }
        }
    }
}

/** Forex Dashboard / Binance Dashboard: one market, equity curve, open positions. */
@Composable
fun MarketDashboardScreen(market: String) {
    val ctx = LocalCtx.current
    val dash = dashboardVm()
    val ds by dash.state.collectAsState()
    val p = (ds as? UiState.Data<Dashboard>)?.value?.portfolios?.filter { it.market == market } ?: emptyList()
    val pid = p.firstOrNull()?.id
    val repo = ctx.app.repo
    val curve: LoadVm<List<ChartPoint>> = viewModel(key = "curve-$market-$pid") {
        LoadVm {
            live(if (pid == null) emptyList() else repo.api.equity(pid, 30).map {
                ChartPoint(Fmt.epochMillis(it.t), it.equity, Fmt.time(it.t))
            })
        }
    }
    val pos = rememberLoad("pos-$market") {
        val ids = api.dashboard().portfolios.filter { it.market == market }.map { it.id }
        live(api.positions("open").filter { it.portfolioId in ids })
    }
    LaunchedEffect(ctx.tick) { if (ctx.tick > 0) { dash.refresh(true); curve.refresh(true) } }
    LoadedList(pos) { open ->
        if (p.isEmpty()) item { Empty("No $market portfolio configured") }
        items(p, key = { it.id }) { PortfolioCard(it) {} }
        item {
            Panel(title = "Equity curve (30 days)") {
                when (val c = curve.state.collectAsState().value) {
                    is UiState.Data -> LineChart(c.value, baseline = p.firstOrNull()?.initialCapital,
                        valueFmt = { Fmt.money(it, p.firstOrNull()?.currency ?: "") })
                    is UiState.Error -> Text(c.message, color = Titan.Down)
                    else -> Text("Loading…", color = Titan.Muted)
                }
            }
        }
        item {
            Panel(title = "Open copied positions (${open.size})") {
                if (open.isEmpty()) Empty("No open positions")
                open.forEach { PositionRow(it, onClick = { ctx.nav.navigate("positions") }) }
            }
        }
        item {
            Panel(title = if (market == "forex") "Forex instruments" else "Binance markets") {
                Text(if (market == "forex") "EUR/USD, GBP/USD, USD/JPY, AUD/USD, USD/CAD, USD/CHF, NZD/USD, EUR/GBP, EUR/JPY, GBP/JPY, XAU/USD · " +
                    "ESMA retail leverage caps 30:1 majors, 20:1 others/gold · positions flattened before the NY 17:00 rollover"
                else "BTC, ETH, BNB, SOL, XRP, DOGE, LINK, ADA, AVAX, LTC (USDT) · filters loaded live from Binance exchangeInfo · " +
                    "one copied position per symbol on futures (one-way mode)",
                    style = MaterialTheme.typography.bodySmall, color = Titan.Muted)
            }
        }
    }
}

@Composable
fun PositionRow(p: com.titan.copyai.data.api.PositionDto, onClick: (() -> Unit)? = null) {
    Column(Modifier.fillMaxWidth().padding(vertical = 6.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Chip(p.side, if (p.side == "buy") Titan.Up else Titan.Down)
            Spacer(Modifier.width(8.dp))
            Text(p.symbol, style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
            SimulatedBadge(p.simulated)
        }
        KV("Qty / entry", "${Fmt.num(p.quantity, 4)} @ ${Fmt.price(p.entryPrice)}")
        KV("SL / TP", "${Fmt.price(p.sl)} / ${Fmt.price(p.tp)}")
        KV("Risk at stop", Fmt.num(p.initialRisk))
        if (p.status == "closed") KV("Realised P&L", Fmt.signed(p.realizedPnl), pnlColor(p.realizedPnl))
        KV("Trader", p.trader ?: "—")
        if (onClick != null) Spacer(Modifier.height(2.dp))
    }
}

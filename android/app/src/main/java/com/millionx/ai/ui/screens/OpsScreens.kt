@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package com.millionx.ai.ui.screens

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.Button
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.navigation.NavController
import com.millionx.ai.data.arr
import com.millionx.ai.data.bool
import com.millionx.ai.data.fmt
import com.millionx.ai.data.get
import com.millionx.ai.data.jsonBody
import com.millionx.ai.data.keys
import com.millionx.ai.data.list
import com.millionx.ai.data.num
import com.millionx.ai.data.numOrNull
import com.millionx.ai.data.pct
import com.millionx.ai.data.str
import com.millionx.ai.ui.components.Banner
import com.millionx.ai.ui.components.Chip
import com.millionx.ai.ui.components.KV
import com.millionx.ai.ui.components.LineChart
import com.millionx.ai.ui.components.LoadBox
import com.millionx.ai.ui.components.LocalAuth
import com.millionx.ai.ui.components.MxCard
import com.millionx.ai.ui.components.RefreshIcon
import com.millionx.ai.ui.components.Screen
import com.millionx.ai.ui.components.SectionTitle
import com.millionx.ai.ui.components.rememberResource
import com.millionx.ai.ui.components.statusColor
import com.millionx.ai.ui.theme.MX
import com.millionx.ai.ui.theme.pnlColor
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonPrimitive

private val BOT_IDS = listOf("bot1_vwap", "bot2_ema", "bot3_orderflow", "bot4_breakout", "bot5_squeeze",
    "bot6_rsidiv", "bot7_liquidity", "bot8_supertrend", "bot9_rangeexp", "bot10_adaptive")

/** QUANT RESEARCH LABORATORY: backtests and walk-forward validation jobs. */
@Composable
fun QuantLabScreen(nav: NavController) {
    val vm = rememberResource("/api/backtests", 10_000)
    val st by vm.state.collectAsState()
    val auth = LocalAuth.current
    var symbols by remember { mutableStateOf("BTCUSDT,ETHUSDT") }
    var start by remember { mutableStateOf(java.time.LocalDate.now().minusDays(90).toString()) }
    var end by remember { mutableStateOf(java.time.LocalDate.now().minusDays(1).toString()) }
    var equity by remember { mutableStateOf("540") }
    var kind by remember { mutableStateOf("backtest") }
    var tf by remember { mutableStateOf(0) }
    val chosen = remember { mutableStateOf(setOf("bot2_ema")) }
    Screen("Quant Research Lab", nav, vm.messages, actions = { RefreshIcon { vm.refresh() } }) {
        item {
            Banner("Real historical 1-minute data is downloaded automatically from Binance's public archive " +
                    "(data.binance.vision, about 2–3 MB per symbol per month) and kept on the phone. Start with ~3 months: " +
                    "multi-year tests take long on a phone. Results are never invented.", MX.Info)
        }
        item {
            MxCard {
                SectionTitle("New job")
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    FilterChip(kind == "backtest", { kind = "backtest" }, { Text("Portfolio backtest") })
                    FilterChip(kind == "walkforward", { kind = "walkforward" }, { Text("Walk-forward + MC") })
                }
                OutlinedTextField(symbols, { symbols = it }, label = { Text("Symbols") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                Text("Timeframe", color = MX.Muted, style = MaterialTheme.typography.bodySmall)
                Row(Modifier.horizontalScroll(androidx.compose.foundation.rememberScrollState()),
                    horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    listOf(0 to "Bot default", 30 to "30 min", 60 to "1 h", 240 to "4 h", 15 to "15 min").forEach { (v, l) ->
                        FilterChip(tf == v, { tf = v }, { Text(l) })
                    }
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedTextField(start, { start = it }, label = { Text("From") }, singleLine = true, modifier = Modifier.weight(1f))
                    OutlinedTextField(end, { end = it }, label = { Text("To") }, singleLine = true, modifier = Modifier.weight(1f))
                }
                if (kind == "backtest") OutlinedTextField(equity, { equity = it }, label = { Text("Start equity (USDT)") },
                    singleLine = true, modifier = Modifier.fillMaxWidth())
                else {
                    Text("Bots to validate", color = MX.Muted, style = MaterialTheme.typography.bodySmall)
                    BOT_IDS.chunked(2).forEach { pair ->
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            pair.forEach { b ->
                                FilterChip(b in chosen.value, {
                                    chosen.value = if (b in chosen.value) chosen.value - b else chosen.value + b
                                }, { Text(b) })
                            }
                        }
                    }
                }
                Button(onClick = {
                    auth("Start research job") {
                        val syms = JsonArray(symbols.split(",").map { JsonPrimitive(it.trim().uppercase()) })
                        val bots = JsonArray(chosen.value.map { JsonPrimitive(it) })
                        val body = jsonBody("kind" to kind, "symbols" to syms, "start" to start, "end" to end,
                            "start_equity" to (equity.toDoubleOrNull() ?: 540.0), "timeframe" to tf,
                            "bots" to if (kind == "walkforward") bots else null)
                        vm.action("POST", "/api/backtests", body, "Job queued")
                    }
                }) { Text("Run") }
            }
        }
        item { SectionTitle("Jobs") }
        item {
            LoadBox(st, { vm.refresh() }) { list ->
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (list.arr.isEmpty()) Text("No jobs yet", color = MX.Muted)
                    list.arr.forEach { j ->
                        MxCard(onClick = { nav.navigate("job/${j.str("id")}") }) {
                            Row(Modifier.fillMaxWidth()) {
                                Text("${j.str("kind")} ${j.str("id")}", fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                                Chip(j.str("status").uppercase(), statusColor(j.str("status")))
                            }
                            Text(j.str("request").take(120), color = MX.Muted, style = MaterialTheme.typography.bodySmall)
                            jobProgress(j)?.let { Text(it, color = MX.Info, style = MaterialTheme.typography.bodySmall) }
                        }
                    }
                }
            }
        }
    }
}

@Composable
fun JobDetailScreen(nav: NavController, id: String) {
    val vm = rememberResource("/api/backtests/$id", 10_000)
    val st by vm.state.collectAsState()
    val auth = LocalAuth.current
    Screen("Job $id", nav, vm.messages) {
        item {
            LoadBox(st, { vm.refresh() }) { j ->
                val r = j["result"]
                Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Chip(j.str("status").uppercase(), statusColor(j.str("status")))
                    if (j.str("status") in setOf("queued", "running")) {
                        Text(jobProgress(j) ?: "starting…", color = MX.Info)
                        androidx.compose.material3.LinearProgressIndicator(Modifier.fillMaxWidth(), color = MX.Accent)
                    }
                    if (r["error"] != null) Banner(r.str("error"), MX.Down)
                    if (j.str("kind") == "backtest" && r["metrics"] != null) {
                        val m = r["metrics"]
                        MxCard {
                            SectionTitle("Portfolio result (net of fees, slippage, funding)")
                            LineChart(r.list("equity_curve").map { (it.arr.getOrNull(1) as? JsonPrimitive)?.content?.toDoubleOrNull() ?: 0.0 })
                            KV("Trades", m.num("trades").toInt().toString())
                            KV("Win rate", m.numOrNull("win_rate").pct(1))
                            KV("Profit factor", m.numOrNull("profit_factor").fmt(2))
                            KV("Expectancy R (95% CI)", "${m.numOrNull("expectancy_r").fmt(3)} ${m.list("r_ci95").joinToString(prefix = "[", postfix = "]") { it.toString().take(6) }}")
                            KV("Sharpe / Sortino", "${m.numOrNull("sharpe").fmt(2)} / ${m.numOrNull("sortino").fmt(2)}")
                            KV("Max DD (closed / MTM)", "${m.numOrNull("max_drawdown").pct(1)} / ${m.numOrNull("max_drawdown_mtm").pct(1)}")
                            KV("Net profit", m.numOrNull("net_profit").fmt(4), pnlColor(m.numOrNull("net_profit")))
                            KV("Fees / funding", "${m.numOrNull("fees").fmt(4)} / ${m.numOrNull("funding").fmt(4)}")
                        }
                        MxCard {
                            SectionTitle("Per bot (shadow book)")
                            val pb = r["per_bot"]
                            pb.keys().forEach { b ->
                                val v = pb[b]["virtual"]
                                KV(b, "n=${v.num("trades").toInt()} PF ${v.numOrNull("profit_factor").fmt(2)} exp ${v.numOrNull("expectancy_r").fmt(2)}R")
                            }
                        }
                        MxCard {
                            SectionTitle("Why signals were rejected")
                            val rej = r["rejections"]
                            rej.keys().forEach { k -> KV(k, rej.num(k).toInt().toString()) }
                        }
                    }
                    if (j.str("kind") == "walkforward" && r != null && r["error"] == null) {
                        r.keys().forEach { b ->
                            val w = r[b]; val act = w["activation"]
                            MxCard {
                                Row(Modifier.fillMaxWidth()) {
                                    Text(b, fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                                    Chip(act.str("status"), statusColor(act.str("status")))
                                }
                                val om = w["oos_metrics"]
                                KV("Out-of-sample trades", om.num("trades").toInt().toString())
                                KV("OOS PF / expR", "${om.numOrNull("profit_factor").fmt(2)} / ${om.numOrNull("expectancy_r").fmt(3)}")
                                KV("Positive windows", w.numOrNull("positive_window_share").pct(0))
                                val mc = w["monte_carlo"]
                                if (mc.bool("available")) KV("Monte Carlo DD p95", mc.numOrNull("max_dd_p95").pct(1))
                                else Text("Monte Carlo: ${mc.str("reason")}", color = MX.Muted, style = MaterialTheme.typography.bodySmall)
                                val ch = act["checks"]
                                ch.keys().forEach { k ->
                                    KV(k, ch[k].str("detail"), if (ch[k].bool("pass")) MX.Up else MX.Down)
                                }
                            }
                        }
                        Button(onClick = { auth("Apply validation") {
                            vm.action("POST", "/api/backtests/$id/apply-validation", "{}", "Validation applied") } }) {
                            Text("Apply verdict to bots")
                        }
                    }
                }
            }
        }
    }
}

/** EXECUTION MONITORING: order journal, latency, scanner. */
@Composable
fun ExecutionScreen(nav: NavController) {
    val orders = rememberResource("/api/orders?limit=100", 10_000, setOf("position_opened", "trade"))
    val scan = rememberResource("/api/scanner", 15_000)
    val o by orders.state.collectAsState()
    val s by scan.state.collectAsState()
    Screen("Execution & Market Scanner", nav, orders.messages, actions = { RefreshIcon { orders.refresh(); scan.refresh() } }) {
        item { SectionTitle("AI market scanner (top-10 liquid perpetuals)") }
        item {
            LoadBox(s, { scan.refresh() }) { list ->
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (list.arr.isEmpty()) Text("Scanner empty: engine not connected to market data.", color = MX.Muted)
                    list.arr.forEach { r ->
                        MxCard {
                            Row(Modifier.fillMaxWidth()) {
                                Text(r.str("symbol"), fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                                if (r.str("regime").isNotEmpty()) Chip(r.str("regime"), when (r.str("regime")) {
                                    "BULL" -> MX.Up; "BEAR" -> MX.Down; else -> MX.Muted })
                                Text(" "); if (r.str("volatility").isNotEmpty()) Chip(r.str("volatility"), MX.Warn)
                            }
                            KV("Price", r.numOrNull("price").fmt(5))
                            KV("Spread / book imbalance", "${r.numOrNull("spread_bps").fmt(2)} bps / ${r.numOrNull("book_imbalance").fmt(2)}")
                            KV("ATR% 15m", r.numOrNull("atr_pct_15m").pct(3))
                            KV("Liquidity OK", if (r.bool("ok", true)) "yes" else "no — ${r.str("reason")}", if (r.bool("ok", true)) MX.Up else MX.Down)
                        }
                    }
                }
            }
        }
        item { SectionTitle("Order journal (exchange-confirmed status)") }
        item {
            LoadBox(o, { orders.refresh() }) { list ->
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (list.arr.isEmpty()) Text("No orders yet", color = MX.Muted)
                    list.arr.forEach { r ->
                        MxCard {
                            Row(Modifier.fillMaxWidth()) {
                                Text("${r.str("side")} ${r.str("symbol")} ${r.str("type")}", modifier = Modifier.weight(1f), fontWeight = FontWeight.SemiBold)
                                Chip(r.str("status"), statusColor(r.str("status")))
                            }
                            KV("Purpose / mode", "${r.str("purpose")} / ${r.str("mode")}")
                            KV("Qty @ avg price", "${r.num("qty")} @ ${r.numOrNull("price").fmt(5)}")
                            KV("Client id", r.str("client_id"))
                            KV("Time", fmtTime(r.num("created_at").toLong()))
                        }
                    }
                }
            }
        }
    }
}

/** TRADE HISTORY with filters. */
@Composable
fun TradesScreen(nav: NavController) {
    var virtual by remember { mutableStateOf("0") }
    val vm = rememberResource("/api/trades?limit=300&virtual=$virtual", 20_000, setOf("trade"))
    val st by vm.state.collectAsState()
    Screen("Trade History", nav, vm.messages, actions = { RefreshIcon { vm.refresh() } }) {
        item {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                FilterChip(virtual == "0", { virtual = "0" }, { Text("Real / paper orders") })
                FilterChip(virtual == "1", { virtual = "1" }, { Text("Shadow competition") })
            }
        }
        item {
            LoadBox(st, { vm.refresh() }) { list ->
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (list.arr.isEmpty()) Text("No trades", color = MX.Muted)
                    list.arr.forEach { TradeRow(it) }
                }
            }
        }
    }
}

@Composable
fun NotificationsScreen(nav: NavController) {
    val vm = rememberResource("/api/notifications?limit=150", 15_000, setOf("alert"))
    val prefs = rememberResource("/api/notifications/prefs", 300_000)
    val st by vm.state.collectAsState()
    val pr by prefs.state.collectAsState()
    Screen("Notifications", nav, vm.messages, actions = {
        TextButton(onClick = { vm.action("POST", "/api/notifications/read", "{}", "Marked read") }) { Text("Read all") }
    }) {
        item {
            LoadBox(pr, { prefs.refresh() }) { p ->
                MxCard {
                    SectionTitle("Push categories")
                    listOf("trade", "risk", "approval", "execution", "bot", "boardroom", "system", "connection").forEach { c ->
                        Row(Modifier.fillMaxWidth()) {
                            Text(c, modifier = Modifier.weight(1f))
                            androidx.compose.material3.Switch(checked = p.bool(c, true), onCheckedChange = { on ->
                                val cur = p.keys().associateWith { p.bool(it, true) }.toMutableMap()
                                cur[c] = on
                                prefs.action("PUT", "/api/notifications/prefs",
                                    kotlinx.serialization.json.JsonObject(cur.mapValues { JsonPrimitive(it.value) }).toString(), "Saved")
                            })
                        }
                    }
                }
            }
        }
        item {
            LoadBox(st, { vm.refresh() }) { list ->
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    list.arr.forEach { n ->
                        MxCard {
                            Row(Modifier.fillMaxWidth()) {
                                Text(n.str("title"), fontWeight = if (n.bool("read")) FontWeight.Normal else FontWeight.Bold,
                                    modifier = Modifier.weight(1f))
                                Chip(n.str("level").uppercase(), statusColor(n.str("level")))
                            }
                            Text(n.str("body"), color = MX.Muted, style = MaterialTheme.typography.bodySmall)
                            Text("${n.str("category")} · ${fmtTime(n.num("ts").toLong())}", color = MX.Muted,
                                style = MaterialTheme.typography.bodySmall)
                        }
                    }
                }
            }
        }
    }
}

/** AUDIT & COMPLIANCE: hash-chained, append-only decision log. */
@Composable
fun AuditScreen(nav: NavController) {
    var actor by remember { mutableStateOf("") }
    val vm = rememberResource("/api/audit?limit=200&actor=$actor", 30_000)
    val ver = rememberResource("/api/audit/verify", 120_000)
    val st by vm.state.collectAsState()
    val v by ver.state.collectAsState()
    Screen("Audit & Compliance", nav, vm.messages, actions = { RefreshIcon { vm.refresh(); ver.refresh() } }) {
        item {
            LoadBox(v, { ver.refresh() }) { r ->
                Banner(if (r.bool("ok")) "Audit chain intact: ${r.num("checked").toInt()} records verified"
                       else "AUDIT CHAIN BROKEN at record ${r.num("broken_at_seq").toInt()}",
                    if (r.bool("ok")) MX.Up else MX.Down)
            }
        }
        item {
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                listOf("" to "All", "decision" to "Decisions", "brain_" to "Brains", "exec_" to "Execs", "owner" to "Owner").forEach { (k, l) ->
                    FilterChip(actor == k, { actor = k }, { Text(l) })
                }
            }
        }
        item {
            LoadBox(st, { vm.refresh() }) { list ->
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    list.arr.forEach { a ->
                        MxCard {
                            Row(Modifier.fillMaxWidth()) {
                                Text(a.str("action"), fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                                Chip(a.str("approval"), statusColor(a.str("approval")))
                            }
                            KV("#${a.num("seq").toInt()} · ${fmtTime(a.num("ts").toLong())}", a.str("actor"))
                            Text("evidence: " + a["evidence"].toString().take(300), color = MX.Muted, style = MaterialTheme.typography.bodySmall)
                            if (a["result"] != null && a["result"].toString() != "null")
                                Text("result: " + a["result"].toString().take(200), style = MaterialTheme.typography.bodySmall)
                            Text("hash ${a.str("hash").take(16)}…", color = MX.Muted, style = MaterialTheme.typography.bodySmall)
                        }
                    }
                }
            }
        }
    }
}


/** Human-readable progress of a research job (download / run stage). */
fun jobProgress(j: kotlinx.serialization.json.JsonElement): String? {
    if (j.str("status") !in setOf("queued", "running")) return null
    val raw = j.str("progress")
    if (raw.isEmpty()) return null
    return runCatching { com.millionx.ai.data.parseJson(raw).str("text") }.getOrNull()?.takeIf { it.isNotEmpty() }
}

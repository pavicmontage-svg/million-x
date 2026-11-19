@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package com.millionx.ai.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.remember
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
import com.millionx.ai.ui.components.BarList
import com.millionx.ai.ui.components.Chip
import com.millionx.ai.ui.components.EmergencyButton
import com.millionx.ai.ui.components.KV
import com.millionx.ai.ui.components.LoadBox
import com.millionx.ai.ui.components.LocalAuth
import com.millionx.ai.ui.components.MxCard
import com.millionx.ai.ui.components.RefreshIcon
import com.millionx.ai.ui.components.Screen
import com.millionx.ai.ui.components.SectionTitle
import com.millionx.ai.ui.components.rememberResource
import com.millionx.ai.ui.theme.MX
import com.millionx.ai.ui.theme.pnlColor
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/** RISK MANAGEMENT CENTER */
@Composable
fun RiskScreen(nav: NavController) {
    val vm = rememberResource("/api/risk", 10_000, setOf("alert", "trade"))
    val st by vm.state.collectAsState()
    val auth = LocalAuth.current
    val edits = remember { mutableStateMapOf<String, String>() }
    val inc = rememberResource("/api/incidents?limit=30", 15_000, setOf("alert"))
    val incidents by inc.state.collectAsState()
    Screen("Risk Management Center", nav, vm.messages, actions = { RefreshIcon { vm.refresh(); inc.refresh() } }) {
        item { com.millionx.ai.ui.components.StaleBanner(vm) }
        item {
            LoadBox(incidents, { inc.refresh() }) { list ->
                val open = list.arr.filter { !it.bool("resolved") }
                if (open.isNotEmpty()) MxCard {
                    SectionTitle("Open execution incidents (${open.size})")
                    open.take(10).forEach { i ->
                        Text("${i.str("severity").uppercase()} · ${i.str("symbol")} · ${i.str("kind")}",
                            color = if (i.str("severity") == "critical") MX.Down else MX.Warn, fontWeight = FontWeight.SemiBold)
                        Text(i.str("detail"), color = MX.Muted, style = MaterialTheme.typography.bodySmall)
                        TextButton(onClick = { auth("Mark incident resolved") {
                            inc.action("POST", "/api/incidents/${i.num("id").toLong()}/resolve", "{}", "Resolved") } }) {
                            Text("Mark resolved")
                        }
                    }
                }
            }
        }
        item {
            LoadBox(st, { vm.refresh() }) { d ->
                val s = d["state"]; val lim = d["limits"]
                Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    if (s.bool("emergency_stop")) Banner("EMERGENCY STOP ACTIVE (${s.str("halted_reason")})", MX.Down)
                    MxCard {
                        SectionTitle("Live risk state")
                        KV("Equity", "${s.numOrNull("equity").fmt(4)} USDT")
                        KV("Drawdown / limit", "${d.numOrNull("drawdown").pct(2)} / ${lim.numOrNull("max_total_drawdown").pct(0)}", MX.Warn)
                        KV("Today / limit", "${d.numOrNull("daily_pnl").pct(2)} / -${lim.numOrNull("max_daily_loss").pct(1)}",
                            pnlColor(d.numOrNull("daily_pnl")))
                        KV("Risk per trade now", d.numOrNull("risk_fraction_now").pct(3))
                        KV("AI risk oversight factor (tighten-only)", d.numOrNull("tighten_factor").fmt(2))
                        Text("All values above are deterministic rule outputs, not AI estimates.", color = MX.Muted,
                            style = MaterialTheme.typography.bodySmall)
                        KV("Consecutive losses", s.num("consecutive_losses").toInt().toString())
                        KV("Open exposure", "${d.numOrNull("exposure").fmt(2)} USDT")
                        KV("Data fresh / connection", "${s.bool("data_fresh")} / ${s.bool("connection_ok")}")
                    }
                    MxCard {
                        SectionTitle("Can 4 EUR / 40 EUR trade? (exchange arithmetic)")
                        val feas = d["feasibility"]
                        if (feas.keys().isEmpty()) Text("Needs live ETHUSDT price (engine running).", color = MX.Muted)
                        feas.keys().forEach { k ->
                            val f = feas[k]
                            Text(k, fontWeight = FontWeight.SemiBold)
                            KV("Feasible", if (f.bool("feasible")) "YES" else "NO", if (f.bool("feasible")) MX.Up else MX.Down)
                            KV("Risk per trade", "${f.numOrNull("risk_per_trade_usdt").fmt(4)} USDT")
                            KV("Exchange min notional", "${f.numOrNull("exchange_min_notional").fmt(2)} USDT")
                            KV("Max notional allowed by 0.5% risk", "${f.numOrNull("max_notional_allowed_by_risk").fmt(2)} USDT")
                            KV("Fees at min size, % of risk", "${f.numOrNull("fees_as_pct_of_risk").fmt(0)}%")
                        }
                        Text(d.str("feasibility_assumption"), color = MX.Muted, style = MaterialTheme.typography.bodySmall)
                    }
                    MxCard {
                        SectionTitle("Limits (owner only, audited; server enforces hard bounds)")
                        Text("Values outside the governance bounds are refused by the server. Changing any limit " +
                                "invalidates a live authorisation.", color = MX.Muted, style = MaterialTheme.typography.bodySmall)
                        lim.keys().forEach { k ->
                            val cur = (lim[k] as? JsonPrimitive)?.content ?: ""
                            OutlinedTextField(value = edits[k] ?: cur, onValueChange = { edits[k] = it }, label = { Text(k) },
                                singleLine = true, modifier = Modifier.fillMaxWidth())
                        }
                        Button(onClick = {
                            auth("Change risk limits") {
                                val body = JsonObject(edits.mapValues { (_, v) ->
                                    v.toDoubleOrNull()?.let { JsonPrimitive(it) } ?: JsonPrimitive(v.toBooleanStrictOrNull() ?: false)
                                }).toString()
                                vm.action("PUT", "/api/risk/limits", body, "Limits saved") { edits.clear() }
                            }
                        }) { Text("Save limits") }
                        TextButton(onClick = { auth("Reset oversight factor") {
                            vm.action("POST", "/api/risk/tighten-reset", "{}", "Oversight factor reset to 1.0") } }) {
                            Text("Reset AI risk tightening to 1.0")
                        }
                    }
                    EmergencyButton { vm.action("POST", "/api/emergency-stop", jsonBody("flatten" to true), "Emergency stop executed") }
                    OutlinedButton(modifier = Modifier.fillMaxWidth(), onClick = {
                        auth("Clear emergency stop") { vm.action("POST", "/api/emergency-reset", "{}", "Emergency cleared") }
                    }) { Text("Clear emergency stop (owner)") }
                }
            }
        }
    }
}

/** PORTFOLIO MANAGEMENT / LIVE TRADING: positions, shadow book, weights. */
@Composable
fun PortfolioScreen(nav: NavController) {
    val pos = rememberResource("/api/positions", 5_000, setOf("position_opened", "trade", "stop_moved"))
    val perf = rememberResource("/api/performance", 60_000, setOf("trade"))
    val comp = rememberResource("/api/strategies/compare", 60_000)
    val p by pos.state.collectAsState()
    val pf by perf.state.collectAsState()
    val c by comp.state.collectAsState()
    val auth = LocalAuth.current
    Screen("Portfolio & Live Trading", nav, pos.messages, actions = { RefreshIcon { pos.refresh(); perf.refresh() } }) {
        item { SectionTitle("Open positions") }
        item {
            LoadBox(p, { pos.refresh() }) { d ->
                Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    if (d.list("open").isEmpty()) Text("No open positions", color = MX.Muted)
                    d.list("open").forEach { x ->
                        MxCard {
                            Row(Modifier.fillMaxWidth()) {
                                Text("${x.str("side")} ${x.str("symbol")}", fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                                Chip(x.str("bot_id"), MX.Purple)
                            }
                            KV("Qty (initial)", "${x.num("qty")} (${x.num("qty_initial")})")
                            KV("Entry", x.num("entry_price").fmt(5))
                            KV("Stop (exchange-side)", x.num("stop").fmt(5), MX.Down)
                            KV("TP1 / TP2", "${x.num("tp1").fmt(5)} / ${x.num("tp2").fmt(5)}", MX.Up)
                            KV("Leverage / risk", "${x.num("leverage").toInt()}x / ${x.num("risk_amount").fmt(4)}")
                            KV("Partial taken · trailing", "${x.bool("tp1_done")} · ${x.bool("trail_active")}")
                            TextButton(onClick = { auth("Close position") {
                                pos.action("POST", "/api/positions/${x.str("symbol")}/close", "{}", "Close sent") } }) {
                                Text("Close at market", color = MX.Down)
                            }
                        }
                    }
                    SectionTitle("Shadow (competition) positions: ${d.list("shadow").size}")
                }
            }
        }
        item { SectionTitle("Performance analytics") }
        item {
            LoadBox(pf, { perf.refresh() }) { d ->
                val o = d["overall"]
                MxCard {
                    KV("Trades", o.num("trades").toInt().toString())
                    KV("Win rate", o.numOrNull("win_rate").pct(1))
                    KV("Profit factor", o.numOrNull("profit_factor").fmt(2))
                    KV("Expectancy", "${o.numOrNull("expectancy").fmt(4)} (${o.numOrNull("expectancy_r").fmt(2)}R)")
                    KV("Sharpe / Sortino", "${o.numOrNull("sharpe").fmt(2)} / ${o.numOrNull("sortino").fmt(2)}")
                    KV("Max drawdown", o.numOrNull("max_drawdown").pct(1))
                    KV("Avg profit / loss", "${o.numOrNull("avg_win").fmt(4)} / ${o.numOrNull("avg_loss").fmt(4)}")
                    KV("Net profit after all fees", o.numOrNull("net_profit").fmt(4), pnlColor(o.numOrNull("net_profit")))
                    KV("Fees / funding", "${o.numOrNull("fees").fmt(4)} / ${o.numOrNull("funding").fmt(4)}")
                }
            }
        }
        item { SectionTitle("Capital weights by strategy (competition)") }
        item {
            LoadBox(c, { comp.refresh() }) { d ->
                MxCard {
                    BarList(d.list("leaderboard").map { it.str("bot_id").substringAfter("_") to it.num("weight") }, max = 2.0)
                    Text("Weights never raise risk above the 0.5% base; they only scale it down.", color = MX.Muted,
                        style = MaterialTheme.typography.bodySmall)
                }
            }
        }
    }
}

/** STRATEGY CENTER: leaderboard, best bot per regime, signal correlation. */
@Composable
fun StrategyCenterScreen(nav: NavController) {
    val vm = rememberResource("/api/strategies/compare", 60_000)
    val st by vm.state.collectAsState()
    Screen("Strategy Center", nav, vm.messages, actions = { RefreshIcon { vm.refresh() } }) {
        item {
            LoadBox(st, { vm.refresh() }) { d ->
                Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    MxCard {
                        SectionTitle("Competition leaderboard (shadow trades)")
                        d.list("leaderboard").forEach { r ->
                            KV(r.str("bot_id"), "n=${r.num("trades").toInt()} PF ${r.numOrNull("profit_factor").fmt(2)} " +
                                    "exp ${r.numOrNull("expectancy_r").fmt(2)}R w=${r.num("weight").fmt(2)}")
                        }
                    }
                    MxCard {
                        SectionTitle("Best validated bot per regime")
                        val best = d["best_by_regime"]
                        if (best.keys().isEmpty()) Text("Not enough closed trades per regime yet.", color = MX.Muted)
                        best.keys().forEach { k -> KV(k, best.str(k, "none qualifies")) }
                    }
                    MxCard {
                        SectionTitle("Signal correlation (share of coinciding signals)")
                        val m = d["signal_correlation"]
                        m.keys().forEach { a ->
                            val row = m[a]
                            val hi = row.keys().filter { it != a && row.num(it) >= 0.5 }
                            if (hi.isNotEmpty()) KV(a, hi.joinToString { "$it ${row.num(it).fmt(2)}" }, MX.Warn)
                        }
                        Text("Pairs ≥ 0.5 are treated as one opinion by the supervisor.", color = MX.Muted,
                            style = MaterialTheme.typography.bodySmall)
                    }
                    MxCard {
                        SectionTitle("Return correlation (daily R, shadow books, 30 d)")
                        val rc = d["return_correlation"]
                        var any = false
                        rc.keys().forEach { a ->
                            val row = rc[a]
                            val hi = row.keys().filter { it > a && (row.numOrNull(it) ?: 0.0) >= 0.6 }
                            if (hi.isNotEmpty()) { any = true; KV(a, hi.joinToString { "$it ${row.numOrNull(it).fmt(2)}" }, MX.Warn) }
                        }
                        if (!any) Text("No strongly correlated pairs (or not enough days of data).", color = MX.Muted)
                    }
                    MxCard {
                        SectionTitle("Daily weight cycles")
                        d.list("history").takeLast(7).forEach { h ->
                            KV(fmtTime(h.num("ts").toLong()), if (h.bool("applied")) "applied" else "kept old (sim worse)")
                        }
                    }
                }
            }
        }
    }
}

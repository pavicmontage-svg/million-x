@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package com.millionx.ai.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.navigation.NavController
import com.millionx.ai.data.arr
import com.millionx.ai.data.bool
import com.millionx.ai.data.fmt
import com.millionx.ai.data.get
import com.millionx.ai.data.jsonBody
import com.millionx.ai.data.list
import com.millionx.ai.data.num
import com.millionx.ai.data.numOrNull
import com.millionx.ai.data.pct
import com.millionx.ai.data.str
import com.millionx.ai.ui.components.Banner
import com.millionx.ai.ui.components.Chip
import com.millionx.ai.ui.components.EmergencyButton
import com.millionx.ai.ui.components.GoalRing
import com.millionx.ai.ui.components.KV
import com.millionx.ai.ui.components.LineChart
import com.millionx.ai.ui.components.LoadBox
import com.millionx.ai.ui.components.LocalAuth
import com.millionx.ai.ui.components.MxCard
import com.millionx.ai.ui.components.RefreshIcon
import com.millionx.ai.ui.components.Screen
import com.millionx.ai.ui.components.SectionTitle
import com.millionx.ai.ui.components.Stat
import com.millionx.ai.ui.components.StatusDot
import com.millionx.ai.ui.components.rememberResource
import com.millionx.ai.ui.components.statusColor
import com.millionx.ai.ui.theme.MX
import com.millionx.ai.ui.theme.Mono
import com.millionx.ai.ui.theme.pnlColor
import com.millionx.ai.ui.vm.Load
import com.millionx.ai.ui.vm.LiveVM
import androidx.lifecycle.viewmodel.compose.viewModel
import kotlinx.serialization.json.JsonElement

@Composable
fun DashboardScreen(nav: NavController) {
    val vm = rememberResource("/api/dashboard", 5_000, setOf("equity", "trade", "position_opened", "signal", "alert"))
    val eqVm = rememberResource("/api/equity?hours=24", 60_000, setOf("trade"))
    val live: LiveVM = viewModel()
    val connected by live.connected.collectAsState()
    val state by vm.state.collectAsState()
    val eq by eqVm.state.collectAsState()
    val auth = LocalAuth.current

    Screen("MILLION-X AI", nav, vm.messages, actions = {
        StatusDot(connected, if (connected) "live" else "offline")
        RefreshIcon { vm.refresh(); eqVm.refresh() }
    }) {
        item {
            LoadBox(state, { vm.refresh() }) { d ->
                androidx.compose.foundation.layout.Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    if (!d.bool("engine_running")) Banner("Trading engine not running: ${d.str("engine_error", "unknown")}", MX.Down)
                    if (d.bool("emergency_stop")) Banner("EMERGENCY STOP ACTIVE — no new trades. Reset in Risk Center.", MX.Down)
                    if (d["data_fresh"] != null && !d.bool("data_fresh")) Banner("Market data stale: new entries blocked.")
                    if (d["connection_ok"] != null && !d.bool("connection_ok")) Banner("Exchange connection lost: new entries blocked.", MX.Down)
                    d.str("halted_reason").takeIf { it.isNotEmpty() }?.let { Banner("Trading halted: $it") }

                    MxCard {
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Chip(d.str("mode").uppercase(), if (d.str("mode") == "live") MX.Down else MX.Info)
                            Chip(d.str("execution").uppercase(), MX.Purple)
                            Chip(d.str("capital_mode").uppercase(), MX.Muted)
                        }
                        Text("Total capital", color = MX.Muted, style = MaterialTheme.typography.labelSmall)
                        Text("€ ${d.numOrNull("equity_eur").fmt(2)}", style = MaterialTheme.typography.headlineLarge, fontFamily = Mono)
                        Text("${d.numOrNull("equity_usdt").fmt(2)} USDT", color = MX.Muted, fontFamily = Mono)
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            Stat("Start", "€ ${d.numOrNull("start_capital_eur").fmt(2)}", Modifier.weight(1f))
                            Stat("P/L", "${d.numOrNull("pnl_usdt").fmt(3)} $", Modifier.weight(1f), pnlColor(d.numOrNull("pnl_usdt")))
                            Stat("Today", "${d.numOrNull("daily_pnl_pct").fmt(2)}%", Modifier.weight(1f), pnlColor(d.numOrNull("daily_pnl_pct")))
                            Stat("Drawdown", "${d.numOrNull("drawdown_pct").fmt(2)}%", Modifier.weight(1f), MX.Warn)
                        }
                    }

                    MxCard {
                        SectionTitle("Equity — last 24 h")
                        val pts = ((eq as? Load.Ok<*>)?.data as? JsonElement).arr.map { it.num("equity") }
                        LineChart(pts, baseline = pts.firstOrNull())
                    }

                    MxCard {
                        SectionTitle("Progress to €1,048,576")
                        val prog = (d.num("goal_progress_pct") / 100).toFloat()
                        GoalRing(prog, label = "${d.num("goal_progress_doublings").fmt(2)} / 18 doublings")
                        val gp = d["goal_probability"]
                        if (gp.bool("available")) {
                            KV("Probability (Monte Carlo, real trades)", gp.numOrNull("prob_goal").pct(3), MX.Warn)
                            KV("Based on trades", gp.num("source_trades").toInt().toString())
                            KV("Risk of ruin (50% DD)", gp.numOrNull("prob_ruin").pct(1))
                        } else {
                            Text("Probability: not estimable yet", fontWeight = FontWeight.SemiBold)
                            Text(gp.str("reason"), color = MX.Muted, style = MaterialTheme.typography.bodySmall)
                            KV("Doublings needed", gp.numOrNull("needed_doublings").fmt(1))
                            KV("1R wins in a row per doubling @0.5%", gp.numOrNull("consecutive_1R_wins_per_doubling").fmt(0))
                        }
                        Text("Experimental target, not a forecast or promise.", color = MX.Muted, style = MaterialTheme.typography.bodySmall)
                    }

                    MxCard {
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            Stat("Active bots", d.num("active_bots").toInt().toString(), Modifier.weight(1f))
                            Stat("Open", d.list("open_positions").size.toString(), Modifier.weight(1f))
                            Stat("Trades", d.num("total_trades").toInt().toString(), Modifier.weight(1f))
                            Stat("Win rate", d.numOrNull("win_rate").pct(0), Modifier.weight(1f))
                        }
                    }

                    val pend = d.list("pending_approvals")
                    if (pend.isNotEmpty()) {
                        SectionTitle("Awaiting your approval")
                        pend.forEach { p ->
                            val s = p["signal"]; val dec = p["decision"]
                            MxCard {
                                Text("${s.str("side")} ${s.str("symbol")} · ${s.str("bot_id")}", fontWeight = FontWeight.SemiBold)
                                Text(s.str("reason"), color = MX.Muted, style = MaterialTheme.typography.bodySmall)
                                KV("Qty / leverage", "${dec.num("qty")} / ${dec.num("leverage").toInt()}x")
                                KV("Risk", "${dec.num("risk_amount").fmt(4)} USDT")
                                KV("Net reward/risk", dec.num("net_reward_risk").fmt(2))
                                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                    OutlinedButton(onClick = {
                                        auth("Approve trade") {
                                            vm.action("POST", "/api/approvals/${s.str("id")}", jsonBody("approve" to true), "Approved")
                                        }
                                    }) { Text("Approve", color = MX.Up) }
                                    TextButton(onClick = {
                                        vm.action("POST", "/api/approvals/${s.str("id")}", jsonBody("approve" to false), "Rejected")
                                    }) { Text("Reject", color = MX.Down) }
                                }
                            }
                        }
                    }

                    SectionTitle("Open positions") { TextButton(onClick = { nav.navigate("portfolio") }) { Text("All") } }
                    val pos = d.list("open_positions")
                    if (pos.isEmpty()) Text("No open positions", color = MX.Muted)
                    pos.forEach { p ->
                        MxCard(onClick = { nav.navigate("portfolio") }) {
                            Row(Modifier.fillMaxWidth()) {
                                Text("${p.str("side")} ${p.str("symbol")}", fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                                Chip(p.str("bot_id"), MX.Purple)
                            }
                            KV("Entry / stop", "${p.num("entry_price").fmt(4)} / ${p.num("stop").fmt(4)}")
                            KV("Qty · lev", "${p.num("qty")} · ${p.num("leverage").toInt()}x")
                        }
                    }

                    SectionTitle("Latest signals")
                    d.list("latest_signals").take(6).forEach { r ->
                        val s = r["signal"]; val v = r["verdict"]
                        val acc = v.bool("accepted")
                        MxCard {
                            Row(Modifier.fillMaxWidth()) {
                                Text("${s.str("side")} ${s.str("symbol")}", modifier = Modifier.weight(1f), fontWeight = FontWeight.SemiBold)
                                Chip(if (acc) "SELECTED" else "FILTERED", if (acc) MX.Up else MX.Muted)
                            }
                            Text("${s.str("bot_id")} — ${s.str("reason")}", color = MX.Muted, style = MaterialTheme.typography.bodySmall)
                            Text(v.list("reasons").joinToString { it.toString().trim('"') }, style = MaterialTheme.typography.bodySmall,
                                color = statusColor(if (acc) "ok" else "warning"))
                        }
                    }
                    EmergencyButton { vm.action("POST", "/api/emergency-stop", jsonBody("flatten" to true), "Emergency stop executed") }
                }
            }
        }
    }
}

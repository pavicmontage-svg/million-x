package com.titan.copyai.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Slider
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.titan.copyai.data.api.AllocationIn
import com.titan.copyai.ui.components.Chip
import com.titan.copyai.ui.components.ConfirmPhraseDialog
import com.titan.copyai.ui.components.Empty
import com.titan.copyai.ui.components.Fmt
import com.titan.copyai.ui.components.KV
import com.titan.copyai.ui.components.Metric
import com.titan.copyai.ui.components.Panel
import com.titan.copyai.ui.components.SimulatedBadge
import com.titan.copyai.ui.components.pnlColor
import com.titan.copyai.ui.components.statusColor
import com.titan.copyai.ui.nav.LocalCtx
import com.titan.copyai.ui.theme.Titan
import com.titan.copyai.ui.vm.live

data class PortfolioBundle(val dashboard: com.titan.copyai.data.api.Dashboard,
                           val allocations: List<com.titan.copyai.data.api.AllocationDto>,
                           val traders: List<com.titan.copyai.data.api.TraderRow>)

@Composable
fun CopyPortfolioScreen() {
    val ctx = LocalCtx.current
    val vm = rememberLoad("copy-portfolio") { live(PortfolioBundle(api.dashboard(), api.allocations(), api.traders())) }
    LoadedList(vm) { b ->
        b.dashboard.portfolios.forEach { p ->
            item(key = "p${p.id}") {
                Panel(title = "${p.name} (${p.currency})", trailing = { SimulatedBadge(p.simulated) }) {
                    val allocs = b.allocations.filter { it.portfolioId == p.id }
                    if (allocs.isEmpty()) Empty("Not following any trader yet")
                    allocs.forEach { a ->
                        var fraction by remember(a.id) { mutableFloatStateOf(a.capitalFraction.toFloat()) }
                        var risk by remember(a.id) { mutableFloatStateOf(a.maxRiskPct.toFloat()) }
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(a.traderName, style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
                            Switch(checked = a.enabled, onCheckedChange = { en ->
                                vm.action(if (en) "Copying enabled" else "Copying disabled") {
                                    ctx.app.repo.api.upsertAllocation(AllocationIn(p.id, a.traderId, en, a.capitalFraction, a.maxRiskPct))
                                }
                            })
                        }
                        if (a.stoppedReason != null) Text(a.stoppedReason, color = Titan.Warn, style = MaterialTheme.typography.bodySmall)
                        Text("Capital allocation ${Fmt.pct(fraction * 100.0, 0)}", style = MaterialTheme.typography.labelMedium)
                        Slider(fraction, { fraction = it }, valueRange = 0.1f..1f, steps = 8, onValueChangeFinished = {
                            vm.action("Allocation saved") {
                                ctx.app.repo.api.upsertAllocation(AllocationIn(p.id, a.traderId, a.enabled, fraction.toDouble(), risk.toDouble()))
                            }
                        })
                        Text("Max open risk for this trader ${Fmt.pct(risk.toDouble())} (ceiling 0.5%)", style = MaterialTheme.typography.labelMedium)
                        Slider(risk, { risk = it }, valueRange = 0.1f..0.5f, steps = 3, onValueChangeFinished = {
                            vm.action("Risk saved") {
                                ctx.app.repo.api.upsertAllocation(AllocationIn(p.id, a.traderId, a.enabled, fraction.toDouble(), risk.toDouble()))
                            }
                        })
                        Spacer(Modifier.height(10.dp))
                    }
                    val candidates = b.traders.filter { t ->
                        t.market == p.market && t.status == "approved" && t.copyCapability == "authorized_signals" &&
                            b.allocations.none { it.portfolioId == p.id && it.traderId == t.id }
                    }
                    if (candidates.isNotEmpty()) Text("Add an approved, authorised trader:", style = MaterialTheme.typography.labelMedium)
                    candidates.forEach { t ->
                        TextButton({ vm.action("Now following ${t.name}") { ctx.app.repo.api.upsertAllocation(AllocationIn(p.id, t.id)) } }) {
                            Text("+ ${t.name} (score ${Fmt.num(t.score, 0)})")
                        }
                    }
                    Text("Only APPROVED traders with an authorised signal source can be copied. Position size is always " +
                        "calculated independently from equity, stop distance and limits - never copied from the source lot size.",
                        style = MaterialTheme.typography.bodySmall, color = Titan.Muted)
                }
            }
        }
    }
}

@Composable
fun PositionsScreen() {
    val ctx = LocalCtx.current
    val vm = rememberLoad("positions-open") { live(api.positions("open")) }
    var confirm by remember { mutableStateOf<Long?>(null) }
    LoadedList(vm) { list ->
        item {
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Metric("Open positions", list.size.toString(), Modifier.weight(1f))
                Metric("Total risk at stops", Fmt.num(list.sumOf { it.initialRisk }), Modifier.weight(1f))
            }
        }
        if (list.isEmpty()) item { Empty("No open copied positions") }
        items(list, key = { it.id }) { p ->
            Panel(title = "#${p.id} · portfolio ${p.portfolioId}", trailing = { SimulatedBadge(p.simulated) }) {
                PositionRow(p)
                KV("Opened", Fmt.time(p.openedAt))
                KV("Copy delay", p.copyDelayMs?.let { "${Fmt.num(it, 0)} ms" } ?: "—")
                p.liquidationPrice?.let { KV("Liquidation price", Fmt.price(it), Titan.Down) }
                OutlinedButton({ confirm = p.id }) { Text("Close position") }
            }
        }
    }
    confirm?.let { id ->
        ConfirmPhraseDialog("Close position #$id", "A market order will close this copied position on the venue.", "CLOSE",
            "Close", onDismiss = { confirm = null }) {
            confirm = null
            vm.action("Close requested") { ctx.app.repo.api.closePosition(id) }
        }
    }
}

@Composable
fun HistoryScreen() {
    val vm = rememberLoad("positions-closed") { live(api.positions("closed", limit = 500)) }
    LoadedList(vm) { list ->
        item {
            val pnl = list.sumOf { it.realizedPnl }
            val wins = list.count { it.realizedPnl > 0 }
            Panel(title = "Summary") {
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    Metric("Trades", list.size.toString(), Modifier.weight(1f))
                    Metric("Net P&L", Fmt.signed(pnl), Modifier.weight(1f), pnlColor(pnl))
                    Metric("Win rate", if (list.isEmpty()) "—" else Fmt.pct(wins * 100.0 / list.size, 0), Modifier.weight(1f))
                    Metric("Fees", Fmt.num(list.sumOf { it.fees }), Modifier.weight(1f))
                }
            }
        }
        if (list.isEmpty()) item { Empty("No closed trades yet") }
        items(list, key = { it.id }) { p ->
            Panel {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Chip(p.side, if (p.side == "buy") Titan.Up else Titan.Down)
                    Spacer(Modifier.width(8.dp))
                    Text(p.symbol, style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
                    Text(Fmt.signed(p.realizedPnl), color = pnlColor(p.realizedPnl), style = MaterialTheme.typography.titleMedium)
                }
                KV("Entry → exit", "${Fmt.price(p.entryPrice)} → ${Fmt.price(p.exitPrice)}")
                KV("R multiple", Fmt.num(p.rMultiple), pnlColor(p.rMultiple))
                KV("Fees / slippage", "${Fmt.num(p.fees)} / ${Fmt.num(p.slippageCost)}")
                KV("Reason", p.closeReason ?: "—", statusColor(p.closeReason ?: ""))
                KV("Closed", Fmt.time(p.closedAt))
                KV("Trader", p.trader ?: "—")
                SimulatedBadge(p.simulated)
            }
        }
    }
}

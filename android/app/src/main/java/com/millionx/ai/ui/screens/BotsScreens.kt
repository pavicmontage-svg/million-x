@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package com.millionx.ai.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
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
import com.millionx.ai.data.keys
import com.millionx.ai.data.num
import com.millionx.ai.data.numOrNull
import com.millionx.ai.data.pct
import com.millionx.ai.data.str
import com.millionx.ai.ui.components.Banner
import com.millionx.ai.ui.components.Chip
import com.millionx.ai.ui.components.KV
import com.millionx.ai.ui.components.LoadBox
import com.millionx.ai.ui.components.LocalAuth
import com.millionx.ai.ui.components.MxCard
import com.millionx.ai.ui.components.RefreshIcon
import com.millionx.ai.ui.components.Screen
import com.millionx.ai.ui.components.SectionTitle
import com.millionx.ai.ui.components.Stat
import com.millionx.ai.ui.components.rememberResource
import com.millionx.ai.ui.components.statusColor
import com.millionx.ai.ui.theme.MX
import com.millionx.ai.ui.theme.pnlColor
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

@Composable
fun BotsScreen(nav: NavController) {
    val vm = rememberResource("/api/bots", 15_000, setOf("trade", "signal"))
    val state by vm.state.collectAsState()
    Screen("Ten-Bot Trading Floor", nav, vm.messages, actions = { RefreshIcon { vm.refresh() } }) {
        item {
            TextLink("Strategy Center: compare bots by regime →") { nav.navigate("strategies") }
        }
        item {
            LoadBox(state, { vm.refresh() }) { data ->
                androidx.compose.foundation.layout.Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    data.arr.forEach { b -> BotCard(b) { nav.navigate("bot/${b.str("bot_id")}") } }
                }
            }
        }
    }
}

@Composable
private fun BotCard(b: JsonElement, onClick: () -> Unit) {
    val real = b["real"]; val virt = b["virtual"]
    MxCard(onClick = onClick) {
        Row(Modifier.fillMaxWidth()) {
            androidx.compose.foundation.layout.Column(Modifier.weight(1f)) {
                Text(b.str("name"), fontWeight = FontWeight.SemiBold)
                Text("${b.str("bot_id")} · ${b.num("timeframe").toInt()}m · weight ${b.num("weight").fmt(2)}",
                    color = MX.Muted, style = MaterialTheme.typography.bodySmall)
            }
            Chip(b.str("status"), statusColor(b.str("status")))
        }
        val sig = b["current_signal"]
        if (sig != null) Text("Signal: ${sig["signal"].str("side")} ${sig["signal"].str("symbol")}",
            color = MX.Info, style = MaterialTheme.typography.bodySmall)
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Stat("Win rate", real.numOrNull("win_rate").pct(0), Modifier.weight(1f))
            Stat("PF", real.numOrNull("profit_factor").fmt(2), Modifier.weight(1f))
            Stat("Trades", real.num("trades").toInt().toString(), Modifier.weight(1f))
            Stat("Net", real.numOrNull("net_profit").fmt(3), Modifier.weight(1f), pnlColor(real.numOrNull("net_profit")))
        }
        Text("Shadow: ${virt.num("trades").toInt()} trades · PF ${virt.numOrNull("profit_factor").fmt(2)} · " +
                "exp ${virt.numOrNull("expectancy_r").fmt(2)}R", color = MX.Muted, style = MaterialTheme.typography.bodySmall)
    }
}

@Composable
fun TextLink(text: String, onClick: () -> Unit) {
    androidx.compose.material3.TextButton(onClick = onClick) { Text(text, color = MX.Accent) }
}

@Composable
fun BotDetailScreen(nav: NavController, botId: String) {
    val vm = rememberResource("/api/bots", 15_000, setOf("trade"))
    val tvm = rememberResource("/api/bots/$botId/trades?limit=100", 30_000, setOf("trade"))
    val state by vm.state.collectAsState()
    val trades by tvm.state.collectAsState()
    val auth = LocalAuth.current
    val edits = remember { mutableStateMapOf<String, String>() }
    Screen(botId, nav, vm.messages) {
        item {
            LoadBox(state, { vm.refresh() }) { all ->
                val b = all.arr.firstOrNull { it.str("bot_id") == botId }
                if (b == null) { Text("Unknown bot"); return@LoadBox }
                androidx.compose.foundation.layout.Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    MxCard {
                        Text(b.str("name"), style = MaterialTheme.typography.headlineSmall)
                        Text(b.str("description"), color = MX.Muted)
                        if (b.bool("is_replacement")) Banner("Slot 9 replacement — this is NOT Moneyline Breakout Entry v3.5. " +
                                "Supply the original source to implement it.", MX.Warn)
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Chip(b.str("status"), statusColor(b.str("status")))
                            Chip("${b.num("timeframe").toInt()}m", MX.Info)
                        }
                        if (b.str("status_reason").isNotEmpty()) Text(b.str("status_reason"), color = MX.Muted,
                            style = MaterialTheme.typography.bodySmall)
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            OutlinedButton(onClick = { auth("Enable bot") {
                                vm.action("POST", "/api/bots/$botId/status", """{"action":"enable"}""", "Enabled (paper)") } }) { Text("Enable") }
                            OutlinedButton(onClick = { auth("Disable bot") {
                                vm.action("POST", "/api/bots/$botId/status", """{"action":"disable"}""", "Disabled") } }) { Text("Disable", color = MX.Down) }
                        }
                        OutlinedButton(onClick = { auth("Mark live-eligible") {
                            vm.action("POST", "/api/bots/$botId/status", """{"action":"mark_live_eligible"}""", "Live-eligible") } }) {
                            Text("Mark live-eligible (needs validation record)")
                        }
                    }
                    listOf("real" to "Real / paper orders", "virtual" to "Shadow competition book").forEach { (k, label) ->
                        val m = b[k]
                        MxCard {
                            SectionTitle(label)
                            KV("Trades", m.num("trades").toInt().toString())
                            KV("Win rate", m.numOrNull("win_rate").pct(1))
                            KV("Profit factor", m.numOrNull("profit_factor").fmt(2))
                            KV("Expectancy (R)", m.numOrNull("expectancy_r").fmt(3))
                            KV("Sharpe / Sortino", "${m.numOrNull("sharpe").fmt(2)} / ${m.numOrNull("sortino").fmt(2)}")
                            KV("Max drawdown", m.numOrNull("max_drawdown").pct(1))
                            KV("Avg win / loss", "${m.numOrNull("avg_win").fmt(4)} / ${m.numOrNull("avg_loss").fmt(4)}")
                            KV("Net after fees", m.numOrNull("net_profit").fmt(4), pnlColor(m.numOrNull("net_profit")))
                            KV("Fees", m.numOrNull("fees").fmt(4))
                            val ci = m.get("r_ci95").arr
                            if (ci.size == 2) KV("95% CI of mean R", "${(ci[0] as JsonPrimitive).content.take(6)} … ${(ci[1] as JsonPrimitive).content.take(6)}")
                        }
                    }
                    MxCard {
                        SectionTitle("Parameters (changing them resets status to RESEARCH)")
                        val p = b["params"]
                        p.keys().forEach { key ->
                            val cur = (p[key] as? JsonPrimitive)?.content ?: ""
                            OutlinedTextField(value = edits[key] ?: cur, onValueChange = { edits[key] = it },
                                label = { Text(key) }, singleLine = true, modifier = Modifier.fillMaxWidth())
                        }
                        Button(onClick = {
                            auth("Change parameters") {
                                val body = JsonObject(p.keys().associateWith { k ->
                                    val v = edits[k] ?: (p[k] as? JsonPrimitive)?.content ?: ""
                                    v.toDoubleOrNull()?.let { JsonPrimitive(it) } ?: JsonPrimitive(v)
                                }).toString()
                                vm.action("PUT", "/api/bots/$botId/params", body, "Parameters saved — re-validate the bot")
                            }
                        }) { Text("Save parameters") }
                    }
                }
            }
        }
        item { SectionTitle("Trade history") }
        item {
            LoadBox(trades, { tvm.refresh() }) { list ->
                androidx.compose.foundation.layout.Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (list.arr.isEmpty()) Text("No trades yet", color = MX.Muted)
                    list.arr.forEach { TradeRow(it) }
                }
            }
        }
    }
}

@Composable
fun TradeRow(t: JsonElement) {
    MxCard {
        Row(Modifier.fillMaxWidth()) {
            Text("${t.str("side")} ${t.str("symbol")}", fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
            if (t.bool("virtual")) Chip("SHADOW", MX.Muted)
            Text(" ${t.numOrNull("net_pnl").fmt(4)}", color = pnlColor(t.numOrNull("net_pnl")))
        }
        Text("${t.str("bot_id")} · ${t.str("exit_reason")} · ${t.numOrNull("r_multiple").fmt(2)}R · fees ${t.numOrNull("fees").fmt(4)}",
            color = MX.Muted, style = MaterialTheme.typography.bodySmall)
        Text("${t.numOrNull("entry_price").fmt(4)} → ${t.numOrNull("exit_price").fmt(4)} · ${fmtTime(t.num("closed_at").toLong())}",
            color = MX.Muted, style = MaterialTheme.typography.bodySmall)
    }
}

fun fmtTime(ms: Long): String =
    if (ms <= 0) "—" else java.text.SimpleDateFormat("dd.MM. HH:mm", java.util.Locale.getDefault()).format(java.util.Date(ms))

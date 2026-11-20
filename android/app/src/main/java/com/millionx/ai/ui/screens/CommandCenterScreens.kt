@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package com.millionx.ai.ui.screens

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material3.Button
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.navigation.NavController
import com.millionx.ai.data.bool
import com.millionx.ai.data.fmt
import com.millionx.ai.data.get
import com.millionx.ai.data.jsonBody
import com.millionx.ai.data.keys
import com.millionx.ai.data.list
import com.millionx.ai.data.long
import com.millionx.ai.data.num
import com.millionx.ai.data.numOrNull
import com.millionx.ai.data.pct
import com.millionx.ai.data.str
import com.millionx.ai.ui.components.Banner
import com.millionx.ai.ui.components.Chip
import com.millionx.ai.ui.components.EmergencyButton
import com.millionx.ai.ui.components.KV
import com.millionx.ai.ui.components.LoadBox
import com.millionx.ai.ui.components.LocalAuth
import com.millionx.ai.ui.components.MxCard
import com.millionx.ai.ui.components.RefreshIcon
import com.millionx.ai.ui.components.Screen
import com.millionx.ai.ui.components.SectionTitle
import com.millionx.ai.ui.components.StatusDot
import com.millionx.ai.ui.components.agentStatusLabel
import com.millionx.ai.ui.components.rememberResource
import com.millionx.ai.ui.components.statusColor
import com.millionx.ai.ui.theme.MX
import com.millionx.ai.ui.theme.pnlColor
import kotlinx.serialization.json.JsonElement

/** AI COMMAND CENTER: providers + failover, Independent Trader, Supervisor, six agents, decisions, controls. */
@Composable
fun CommandCenterScreen(nav: NavController) {
    val vm = rememberResource("/api/ai/status", 15_000, setOf("signal", "decision", "alert"))
    val st by vm.state.collectAsState()
    val auth = LocalAuth.current
    var msg by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(vm) { vm.messages.collect { msg = it } }
    Screen("AI Command Center", nav, vm.messages, actions = { RefreshIcon { vm.refresh() } }) {
        item {
            LoadBox(st, { vm.refresh() }) { d ->
                val chain = d["chain"]
                val desk = d["desk"]
                Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    msg?.let { Text(it, color = MX.Info, style = MaterialTheme.typography.bodySmall) }
                    if (!chain.bool("any_configured")) Banner("No AI provider configured: the Independent Trader and the " +
                            "AI Supervisor are off and the deterministic pipeline runs as before. Add a free Groq key below.", MX.Warn)
                    if (chain.bool("paused")) Banner("AI PAUSED by owner — AI decisions are NO TRADE.", MX.Warn)
                    MxCard {
                        SectionTitle("Controls")
                        Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            fun act(a: String, label: String) = auth(label) {
                                vm.action("POST", "/api/ai/control", jsonBody("action" to a), label)
                            }
                            if (chain.bool("paused")) Button(onClick = { act("resume", "Resume AI") }) { Text("Resume AI") }
                            else OutlinedButton(onClick = { act("pause", "Pause AI") }) { Text("Pause AI") }
                            OutlinedButton(onClick = {
                                act(if (desk.bool("trader_active")) "trader_off" else "trader_on", "Independent Trader")
                            }) { Text(if (desk["settings"].bool("trader_enabled")) "Disable Trader" else "Enable Trader") }
                            OutlinedButton(onClick = {
                                act(if (desk["settings"].bool("supervisor_enabled")) "supervisor_off" else "supervisor_on", "Supervisor")
                            }) { Text(if (desk["settings"].bool("supervisor_enabled")) "Disable Supervisor" else "Enable Supervisor") }
                            OutlinedButton(onClick = { act("run_trader_now", "Run trader now") }) { Text("Run trader now") }
                        }
                        Text("Mode Demo/Live is selected in Owner Administration (live needs preflight + owner unlock).",
                            color = MX.Muted, style = MaterialTheme.typography.bodySmall)
                    }
                    MxCard {
                        SectionTitle("Independent Trader & Supervisor")
                        KV("Independent Trader", if (desk.bool("trader_active")) "ACTIVE" else "off",
                            if (desk.bool("trader_active")) MX.Up else MX.Muted)
                        KV("AI Supervisor", if (desk.bool("supervisor_active")) "ACTIVE (gate before risk engine)" else "off",
                            if (desk.bool("supervisor_active")) MX.Up else MX.Muted)
                        KV("Trader interval", "${desk["settings"].num("trader_interval_min").toInt()} min · min confidence " +
                                desk["settings"].num("trader_min_confidence").fmt(2))
                        desk.long("last_trader_run").takeIf { it > 0 }?.let { KV("Last trader run", fmtTime(it)) }
                        val counts = desk.list("counts_24h")
                        val analysed = counts.filter { it.str("agent") == "independent_trader" }.sumOf { it.num("n").toInt() }
                        val approved = counts.filter { it.str("agent") == "supervisor" && it.str("decision") == "APPROVE" }.sumOf { it.num("n").toInt() }
                        val rejected = counts.filter { it.str("agent") == "supervisor" && it.str("decision") != "APPROVE" }.sumOf { it.num("n").toInt() }
                        KV("Trader decisions (24 h)", analysed.toString())
                        KV("Supervisor approved / not approved (24 h)", "$approved / $rejected")
                        desk.list("top_rejection_reasons").forEach { r ->
                            val pair = (r as? kotlinx.serialization.json.JsonArray)
                            Text("• ${pair?.getOrNull(0)?.toString()?.trim('"') ?: ""} ×${pair?.getOrNull(1) ?: ""}",
                                color = MX.Warn, style = MaterialTheme.typography.bodySmall)
                        }
                    }
                    SectionTitle("AI providers (failover order)")
                    chain.list("providers").forEach { p -> ProviderCard(vm, p, chain["usage_today"][p.str("id")]) }
                    MxCard {
                        SectionTitle("Six agents")
                        d.list("agents").filter { it.str("kind") == "brain" }.forEach { a ->
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text(a.str("title"), modifier = Modifier.weight(1f), style = MaterialTheme.typography.bodySmall)
                                StatusDot(a.bool("active"), agentStatusLabel(a.str("status")), statusColor(a.str("status")))
                            }
                        }
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedButton(onClick = { nav.navigate("ai_decisions") }) { Text("Decision history") }
                        OutlinedButton(onClick = { nav.navigate("perf_audit") }) { Text("Performance") }
                    }
                    EmergencyButton { vm.action("POST", "/api/emergency-stop", jsonBody("flatten" to true), "Emergency stop executed") }
                }
            }
        }
    }
}

@Composable
private fun ProviderCard(vm: com.millionx.ai.ui.vm.ResourceVM, p: JsonElement, usage: JsonElement?) {
    val auth = LocalAuth.current
    var key by remember { mutableStateOf("") }
    var model by remember { mutableStateOf(p.str("configured_model").let { if (it == "auto") "" else it }) }
    var open by remember { mutableStateOf(false) }
    MxCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(p.str("label"), fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
            when {
                !p.bool("configured") -> Chip("NOT SET", MX.Muted)
                p.str("unhealthy").isNotEmpty() -> Chip("KEY ERROR", MX.Down)
                p.num("cooldown_s") > 0 -> Chip("LIMIT ${p.num("cooldown_s").toInt()}s", MX.Warn)
                p.bool("available") -> Chip("READY", MX.Up)
                else -> Chip("OFF", MX.Muted)
            }
        }
        if (p.bool("configured")) {
            KV("Active model", p.str("model", "—"))
            usage?.let { u ->
                KV("Today: requests / errors", "${u.num("requests").toInt()} / ${u.num("errors").toInt()}")
                KV("Today: tokens in / out", "${u.num("input_tokens").toLong()} / ${u.num("output_tokens").toLong()}")
            }
            val q = p["quota_headers"]
            q.keys().filter { it.contains("remaining") }.take(3).forEach { k -> KV(k.removePrefix("x-ratelimit-"), q.str(k)) }
            if (p.str("last_error").isNotEmpty()) Text("Last error: ${p.str("last_error")}", color = MX.Warn,
                style = MaterialTheme.typography.bodySmall)
            if (p.str("unhealthy").isNotEmpty()) Text(p.str("unhealthy"), color = MX.Down, style = MaterialTheme.typography.bodySmall)
        }
        Text("Key: ${p.str("key_help")}", color = MX.Muted, style = MaterialTheme.typography.bodySmall)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            TextButton(onClick = { open = !open }) { Text(if (p.bool("configured")) "Change" else "Add key") }
            if (p.bool("configured")) TextButton(onClick = {
                vm.action("POST", "/api/ai/providers/${p.str("id")}/test", "{}", "Test finished")
            }) { Text("Test") }
            if (p.bool("configured")) TextButton(onClick = { auth("Toggle provider") {
                vm.action("PUT", "/api/ai/providers/${p.str("id")}", jsonBody("enabled" to !p.bool("enabled")),
                    if (p.bool("enabled")) "Disabled" else "Enabled") } }) {
                Text(if (p.bool("enabled")) "Disable" else "Enable", color = if (p.bool("enabled")) MX.Down else MX.Up)
            }
        }
        if (open) {
            OutlinedTextField(key, { key = it.trim() }, label = { Text("API key") }, singleLine = true,
                visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth())
            OutlinedTextField(model, { model = it.trim() }, label = { Text("Model (empty = auto from provider's list)") },
                singleLine = true, modifier = Modifier.fillMaxWidth())
            Button(enabled = key.length >= 10 || p.bool("configured"), onClick = {
                auth("Save AI provider") {
                    val body = if (key.isNotEmpty()) jsonBody("api_key" to key, "model" to model, "enabled" to true)
                    else jsonBody("model" to model)
                    vm.action("PUT", "/api/ai/providers/${p.str("id")}", body, "Saved — health check done")
                    key = ""; open = false
                }
            }) { Text("Save & check") }
        }
    }
}

/** Every Independent Trader and Supervisor decision, newest first. */
@Composable
fun AiDecisionsScreen(nav: NavController) {
    var agent by rememberSaveable { mutableStateOf("") }
    val vm = rememberResource("/api/ai/decisions?limit=100" + if (agent.isNotEmpty()) "&agent=$agent" else "", 15_000,
        setOf("signal", "decision"))
    val st by vm.state.collectAsState()
    Screen("AI decision history", nav, vm.messages, actions = { RefreshIcon { vm.refresh() } }) {
        item {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf("" to "All", "independent_trader" to "Trader", "supervisor" to "Supervisor").forEach { (a, l) ->
                    FilterChip(agent == a, { agent = a }, { Text(l) })
                }
            }
        }
        item {
            LoadBox(st, { vm.refresh() }) { list ->
                val arr = (list as? kotlinx.serialization.json.JsonArray).orEmpty()
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (arr.isEmpty()) Text("No AI decisions yet.", color = MX.Muted)
                    arr.forEach { r -> DecisionCard(r) }
                }
            }
        }
    }
}

@Composable
private fun DecisionCard(r: JsonElement) {
    var open by remember { mutableStateOf(false) }
    val p = r["payload"]
    val dec = r.str("decision")
    MxCard(onClick = { open = !open }) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("${if (r.str("agent") == "supervisor") "Supervisor" else "Trader"} · ${r.str("symbol")}",
                fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
            Chip(dec, when (dec) { "APPROVE", "LONG", "SHORT" -> MX.Up; "NO_TRADE", "WAIT" -> MX.Info; else -> MX.Down })
        }
        Text("${fmtTime(r.long("ts"))} · ${r.str("status")} · ${r.str("provider").ifEmpty { "rules" }} ${r.str("model")}",
            color = MX.Muted, style = MaterialTheme.typography.bodySmall)
        if (p.numOrNull("entry") != null) Text("entry ${p.num("entry").fmt(4)} · SL ${p.num("stop_loss").fmt(4)} · " +
                "TP ${p.num("take_profit").fmt(4)} · R:R ${p.num("risk_reward").fmt(2)} · conf ${p.num("confidence").fmt(2)}",
            style = MaterialTheme.typography.bodySmall)
        if (p.numOrNull("max_leverage") != null && dec == "APPROVE") KV("Approved leverage", "${p.num("max_leverage").toInt()}x")
        if (open) {
            p.str("reasoning").takeIf { it.isNotEmpty() }?.let { Text(it, style = MaterialTheme.typography.bodySmall) }
            p.str("invalidation").takeIf { it.isNotEmpty() }?.let { Text("Invalid if: $it", color = MX.Warn,
                style = MaterialTheme.typography.bodySmall) }
            p.list("reasons").forEach { Text("• ${it.toString().trim('"')}", style = MaterialTheme.typography.bodySmall) }
            p.list("disagreements").forEach { Text("≠ ${it.toString().trim('"')}", color = MX.Warn,
                style = MaterialTheme.typography.bodySmall) }
            p.str("check").takeIf { it.isNotEmpty() }?.let { Text("Validation: $it", color = MX.Down,
                style = MaterialTheme.typography.bodySmall) }
            if (r.str("market_data_id").isNotEmpty()) Text("Data: ${r.str("market_data_id")}", color = MX.Muted,
                style = MaterialTheme.typography.bodySmall)
        }
    }
}

/** PERFORMANCE AUDITOR: real vs simulated, by strategy / instrument / regime, AI vs bots. */
@Composable
fun PerformanceAuditScreen(nav: NavController) {
    var days by rememberSaveable { mutableStateOf(30) }
    val vm = rememberResource("/api/performance/audit?days=$days", 60_000, setOf("trade"))
    val st by vm.state.collectAsState()
    Screen("Performance Auditor", nav, vm.messages, actions = { RefreshIcon { vm.refresh() } }) {
        item {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf(1, 7, 30, 90).forEach { n -> FilterChip(days == n, { days = n }, { Text("${n}d") }) }
            }
        }
        item {
            LoadBox(st, { vm.refresh() }) { d ->
                Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Banner("REAL = fills on Binance (${d.str("mode").uppercase()}). SIMULATED = shadow paper books at a " +
                            "fixed 0.5 R stake — never realised profit.", MX.Info)
                    listOf("real" to "REAL", "simulated" to "SIMULATED").forEach { (k, l) ->
                        val sec = d[k]
                        MxCard {
                            SectionTitle("$l — overall")
                            MetricsBlock(sec["overall"])
                            Text(sec.str("note"), color = MX.Muted, style = MaterialTheme.typography.bodySmall)
                            SectionTitle("$l — by strategy / agent")
                            GroupTable(sec["by_strategy"])
                            SectionTitle("$l — by instrument")
                            GroupTable(sec["by_instrument"])
                            SectionTitle("$l — by market regime")
                            GroupTable(sec["by_regime"])
                        }
                    }
                    val ai = d["ai_vs_bots"]
                    MxCard {
                        SectionTitle("AI Independent Trader vs bots (same window & instruments, simulated)")
                        if (ai.str("note").isNotEmpty()) Text(ai.str("note"), color = MX.Muted)
                        else {
                            Text("AI trader", fontWeight = FontWeight.SemiBold)
                            MetricsBlock(ai["ai_trader"])
                            Text("Bots", fontWeight = FontWeight.SemiBold)
                            GroupTable(ai["bots_same_window"])
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun MetricsBlock(m: JsonElement?) {
    KV("Trades", m.num("trades").toInt().toString())
    KV("Win rate", m.numOrNull("win_rate").pct(1))
    KV("Profit factor", m.numOrNull("profit_factor").fmt(2))
    KV("Expectancy", "${m.numOrNull("expectancy_r").fmt(3)} R")
    KV("Avg win / loss", "${m.numOrNull("avg_win").fmt(4)} / ${m.numOrNull("avg_loss").fmt(4)}")
    KV("Max drawdown", m.numOrNull("max_drawdown").pct(1))
    KV("Sharpe / Sortino", "${m.numOrNull("sharpe").fmt(2)} / ${m.numOrNull("sortino").fmt(2)}")
    KV("Net", m.numOrNull("net_profit").fmt(4), pnlColor(m.numOrNull("net_profit")))
}

@Composable
private fun GroupTable(g: JsonElement?) {
    val ks = g.keys()
    if (ks.isEmpty()) {
        Text("no closed trades", color = MX.Muted, style = MaterialTheme.typography.bodySmall)
        return
    }
    ks.sortedByDescending { g[it].num("trades") }.take(15).forEach { k ->
        val m = g[k]
        KV("$k · ${m.num("trades").toInt()} tr · WR ${m.numOrNull("win_rate").pct(0)} · PF ${m.numOrNull("profit_factor").fmt(2)}",
            "${m.numOrNull("expectancy_r").fmt(2)} R", pnlColor(m.numOrNull("expectancy_r")))
    }
}

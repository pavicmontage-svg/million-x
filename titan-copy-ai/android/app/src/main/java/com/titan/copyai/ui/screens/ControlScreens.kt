package com.titan.copyai.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.titan.copyai.ui.components.Chip
import com.titan.copyai.ui.components.ConfirmPhraseDialog
import com.titan.copyai.ui.components.Empty
import com.titan.copyai.ui.components.Fmt
import com.titan.copyai.ui.components.KV
import com.titan.copyai.ui.components.Panel
import com.titan.copyai.ui.components.UNKNOWN
import com.titan.copyai.ui.components.UsageBar
import com.titan.copyai.ui.components.at
import com.titan.copyai.ui.components.b
import com.titan.copyai.ui.components.d
import com.titan.copyai.ui.components.obj
import com.titan.copyai.ui.components.s
import com.titan.copyai.ui.components.statusColor
import com.titan.copyai.ui.nav.LocalCtx
import com.titan.copyai.ui.theme.Titan
import com.titan.copyai.ui.vm.live
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonObject

@Composable
fun RiskScreen() {
    val ctx = LocalCtx.current
    val vm = rememberLoad("risk") { live(api.risk()) }
    var reactivate by remember { mutableStateOf<Pair<Int, String>?>(null) }
    LoadedList(vm) { r ->
        val ports = (r["portfolios"] as? JsonArray)?.mapNotNull { it.obj() } ?: emptyList()
        items(ports, key = { it.at("id").s() ?: "" }) { p ->
            val lim = p.at("limits").obj()
            val status = p.at("status").s() ?: ""
            val pid = p.at("id").s()?.toIntOrNull() ?: 0
            Panel(title = "${p.at("name").s()} · ${p.at("market").s()}", trailing = { Chip(status, statusColor(status)) }) {
                val daily = p.at("daily_loss_pct").d()
                val dl = lim.at("daily_loss_pct").d() ?: 2.0
                UsageBar("Daily loss ${Fmt.pct(daily)} of ${Fmt.pct(dl, 1)} limit", daily?.let { it / dl * 100 })
                Spacer(Modifier.height(6.dp))
                val weekly = p.at("weekly_loss_pct").d(); val wl = lim.at("weekly_loss_pct").d() ?: 4.0
                UsageBar("Weekly loss ${Fmt.pct(weekly)} of ${Fmt.pct(wl, 1)} limit", weekly?.let { it / wl * 100 })
                Spacer(Modifier.height(6.dp))
                val dd = p.at("drawdown_pct").d(); val ml = lim.at("max_drawdown_pct").d() ?: 10.0
                UsageBar("Drawdown ${Fmt.pct(dd)} of ${Fmt.pct(ml, 1)} maximum", dd?.let { it / ml * 100 })
                Spacer(Modifier.height(6.dp))
                val orisk = p.at("open_risk_pct").d(); val pr = lim.at("max_portfolio_risk_pct").d() ?: 1.5
                UsageBar("Open risk ${Fmt.pct(orisk)} of ${Fmt.pct(pr, 1)} portfolio limit", orisk?.let { it / pr * 100 })
                Spacer(Modifier.height(8.dp))
                KV("Risk per trade", Fmt.pct(lim.at("risk_per_trade_pct").d()))
                KV("Max risk per trader", Fmt.pct(lim.at("max_trader_risk_pct").d()))
                KV("Max correlated currency exposure", Fmt.pct(lim.at("max_currency_exposure_pct").d()))
                KV("Max effective leverage", Fmt.num(lim.at("max_effective_leverage").d(), 1) + "x")
                KV("Max margin usage", Fmt.pct(lim.at("max_margin_usage_pct").d(), 0))
                KV("Stop-loss required", if (lim.at("require_stop_loss").b() == true) "ALWAYS" else UNKNOWN)
                KV("Flat before forex rollover", if (p.at("flat_before_rollover").b() == true) "YES" else "NO")
                Text("Limits can only be tightened from the app; the ceilings are hard-coded on the server. Risk is " +
                    "never increased to recover losses (drawdown de-risking: 3% → 75%, 5% → 50%, 7.5% → 25% size).",
                    style = MaterialTheme.typography.bodySmall, color = Titan.Muted)
                Spacer(Modifier.height(8.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedButton({
                        vm.action("Risk per trade set to 0.25%") {
                            ctx.app.repo.api.patchPortfolio(pid, buildJsonObject { putJsonObject("risk_limits") { put("risk_per_trade_pct", 0.25) } })
                        }
                    }) { Text("Halve risk") }
                    if (status == "review_required") Button({ reactivate = pid to (p.at("name").s() ?: "") }) { Text("Review & reactivate") }
                }
            }
        }
        item {
            Panel(title = "AI / calendar overlays (tighten-only)") {
                val blocks = (r.at("overlay", "blocks") as? JsonArray)?.mapNotNull { it.obj() } ?: emptyList()
                if (blocks.isEmpty()) Empty("No active blocks")
                blocks.forEach { b -> KV("${b.at("kind").s()} ${b.at("key").s()}", "until ${Fmt.time(b.at("until").s())}", Titan.Warn) }
                r.at("overlay", "multiplier").obj()?.let { KV("Risk multiplier", Fmt.num(it.at("value").d()), Titan.Warn) }
            }
        }
        item {
            Panel(title = "Risk events") {
                val ev = (r["events"] as? JsonArray)?.mapNotNull { it.obj() } ?: emptyList()
                if (ev.isEmpty()) Empty("No risk events")
                ev.take(40).forEach { e -> KV("${Fmt.time(e.at("ts").s())} ${e.at("kind").s()}", e.at("severity").s() ?: "",
                    statusColor(if (e.at("severity").s() == "critical") "failed" else "")) }
            }
        }
    }
    reactivate?.let { (pid, name) ->
        ConfirmPhraseDialog("Manual review", "The 10% maximum drawdown was reached. Confirm you reviewed the traders, " +
            "positions and logs. Drawdown is re-measured from current equity; risk limits stay unchanged.",
            "REVIEWED $name", "Reactivate", onDismiss = { reactivate = null }) { phrase ->
            reactivate = null
            vm.action("Portfolio reactivated") { ctx.app.repo.api.reactivate(pid, buildJsonObject { put("confirmation", phrase) }) }
        }
    }
}

@Composable
fun EmergencyScreen() {
    val ctx = LocalCtx.current
    val vm = rememberLoad("emergency") { live(api.emergency()) }
    var dialog by remember { mutableStateOf<String?>(null) }
    LoadedList(vm) { e ->
        val active = e.at("kill_switch", "active").b() == true
        item {
            Panel(title = "Global emergency stop") {
                Text(if (active) "ACTIVE - no new positions can be opened" else "Inactive - trading allowed within limits",
                    style = MaterialTheme.typography.titleMedium, color = if (active) Titan.Down else Titan.Up)
                e.at("kill_switch", "reason").s()?.let { Text("Reason: $it", color = Titan.Muted) }
                Spacer(Modifier.height(10.dp))
                if (!active) Button({ vm.action("EMERGENCY STOP ACTIVATED") {
                    ctx.app.repo.api.emergencyStop(buildJsonObject { put("reason", "manual stop from app") }) } },
                    colors = ButtonDefaults.buttonColors(containerColor = Titan.Down), modifier = Modifier.fillMaxWidth()) {
                    Text("STOP ALL NEW TRADING")
                } else OutlinedButton({ dialog = "release" }, modifier = Modifier.fillMaxWidth()) { Text("Release emergency stop") }
            }
        }
        item {
            Panel(title = "Close everything") {
                Text("Open copied positions: ${e.at("open_positions").s()}", style = MaterialTheme.typography.titleMedium)
                Text("Closes every open copied position with market orders in all portfolios.", color = Titan.Muted,
                    style = MaterialTheme.typography.bodySmall)
                Spacer(Modifier.height(8.dp))
                Button({ dialog = "flatten" }, colors = ButtonDefaults.buttonColors(containerColor = Titan.Down),
                    modifier = Modifier.fillMaxWidth()) { Text("CLOSE ALL POSITIONS") }
            }
        }
        item {
            Panel(title = "Portfolios") {
                (e["portfolios"] as? JsonArray)?.mapNotNull { it.obj() }?.forEach { p ->
                    KV(p.at("name").s() ?: "", "${p.at("status").s()} · copy ${if (p.at("copy_enabled").b() == true) "ON" else "OFF"}",
                        statusColor(p.at("status").s() ?: ""))
                }
                Text("Individual trader stops are on the Copy Portfolio screen (switch) and in the trader profile.",
                    color = Titan.Muted, style = MaterialTheme.typography.bodySmall)
            }
        }
    }
    when (dialog) {
        "release" -> ConfirmPhraseDialog("Release emergency stop", "New copied positions will be allowed again (within all risk limits).",
            "RELEASE EMERGENCY STOP", "Release", onDismiss = { dialog = null }) { phrase ->
            dialog = null
            vm.action("Emergency stop released") { ctx.app.repo.api.emergencyRelease(buildJsonObject { put("confirmation", phrase) }) }
        }
        "flatten" -> ConfirmPhraseDialog("Close all positions", "All open copied positions will be closed at market.",
            "CLOSE ALL", "Close all", onDismiss = { dialog = null }) { phrase ->
            dialog = null
            vm.action("Close-all queued") { ctx.app.repo.api.flatten(buildJsonObject { put("confirmation", phrase) }) }
        }
    }
}

@Composable
fun AiCenterScreen() {
    val ctx = LocalCtx.current
    val vm = rememberLoad("agents") { live(api.agents()) }
    var expanded by remember { mutableStateOf<String?>(null) }
    LoadedList(vm) { agents ->
        item {
            Text("Six independent agents. Deterministic analysis always runs; Claude reviews it when an API key is " +
                "configured on the server. Agents can only tighten risk - the hard-coded risk engine has final veto.",
                style = MaterialTheme.typography.bodySmall, color = Titan.Muted)
        }
        items(agents, key = { it.name }) { a ->
            Panel(title = "${a.title} · v${a.version}", onClick = { expanded = if (expanded == a.name) null else a.name }) {
                val last = a.lastRun
                KV("Runs", a.metrics.at("runs").s() ?: "0")
                KV("Avg latency", a.metrics.at("avg_ms").d()?.let { "${Fmt.num(it, 0)} ms" } ?: "—")
                KV("Tokens in / out", "${a.metrics.at("input_tokens").s()} / ${a.metrics.at("output_tokens").s()}")
                KV("Errors", a.metrics.at("errors").s() ?: "0")
                KV("Last run", Fmt.time(last.at("ts").s()))
                KV("LLM", if (last.at("llm_used").b() == true) (last.at("llm_model").s() ?: "") else "offline / deterministic")
                last.at("output", "llm_review", "summary").s()?.let { Text(it, style = MaterialTheme.typography.bodyMedium) }
                last.at("output", "llm_review", "status").s()?.let { Text(it, color = Titan.Muted, style = MaterialTheme.typography.bodySmall) }
                if (expanded == a.name) {
                    Spacer(Modifier.height(8.dp))
                    Text("PROMPT VERSIONS", style = MaterialTheme.typography.labelSmall, color = Titan.Muted)
                    a.promptVersions.forEach { v -> KV(v.at("version").s() ?: "", "${v.at("sha256").s()} · ${Fmt.time(v.at("created_at").s())}") }
                    Spacer(Modifier.height(6.dp))
                    Text(a.prompt, style = MaterialTheme.typography.bodySmall, color = Titan.Muted)
                    Spacer(Modifier.height(6.dp))
                    Text("LAST OUTPUT", style = MaterialTheme.typography.labelSmall, color = Titan.Muted)
                    Text(last.at("output")?.toString()?.take(3000) ?: "—", style = MaterialTheme.typography.bodySmall)
                }
                Spacer(Modifier.height(6.dp))
                OutlinedButton({
                    if (a.name in setOf("trader_intelligence", "quant_research")) {
                        ctx.nav.navigate("traders")
                    } else vm.action("${a.title} finished") {
                        val api = ctx.app.repo.api
                        if (a.name == "risk_management") {
                            api.dashboard().portfolios.forEach { p -> api.runAgent(a.name, buildJsonObject { put("portfolio_id", p.id) }) }
                        } else api.runAgent(a.name, buildJsonObject { })
                    }
                }) { Text(if (a.name in setOf("trader_intelligence", "quant_research")) "Run from a trader profile" else "Run now") }
            }
        }
    }
}

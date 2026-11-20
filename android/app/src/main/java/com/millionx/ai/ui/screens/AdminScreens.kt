@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package com.millionx.ai.ui.screens

import android.os.Build
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AccountBalance
import androidx.compose.material.icons.filled.AdminPanelSettings
import androidx.compose.material.icons.filled.Gavel
import androidx.compose.material.icons.filled.Groups
import androidx.compose.material.icons.filled.History
import androidx.compose.material.icons.filled.Insights
import androidx.compose.material.icons.filled.Notifications
import androidx.compose.material.icons.filled.Psychology
import androidx.compose.material.icons.filled.Radar
import androidx.compose.material.icons.filled.Science
import androidx.compose.material.icons.filled.Shield
import androidx.compose.material.icons.filled.SmartToy
import androidx.compose.material.icons.filled.Speed
import androidx.compose.material3.Button
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import androidx.navigation.NavController
import com.millionx.ai.MillionXApp
import com.millionx.ai.data.arr
import com.millionx.ai.data.bool
import com.millionx.ai.data.get
import com.millionx.ai.data.jsonBody
import com.millionx.ai.data.keys
import com.millionx.ai.data.num
import com.millionx.ai.data.str
import com.millionx.ai.ui.components.Banner
import com.millionx.ai.ui.components.Chip
import com.millionx.ai.ui.components.ConfirmDialog
import com.millionx.ai.ui.components.KV
import com.millionx.ai.ui.components.LoadBox
import com.millionx.ai.ui.components.LocalAuth
import com.millionx.ai.ui.components.MxCard
import com.millionx.ai.ui.components.RefreshIcon
import com.millionx.ai.ui.components.Screen
import com.millionx.ai.ui.components.SectionTitle
import com.millionx.ai.ui.components.rememberResource
import com.millionx.ai.ui.components.statusColor
import com.millionx.ai.ui.theme.MX
import kotlinx.coroutines.launch

/** OWNER ADMINISTRATION: modes, API keys, preflight, live unlock, devices. */
@Composable
fun AdminScreen(nav: NavController, onLogout: () -> Unit) {
    val vm = rememberResource("/api/settings", 30_000)
    val dev = rememberResource("/api/admin/devices", 60_000)
    val st by vm.state.collectAsState()
    val ds by dev.state.collectAsState()
    val auth = LocalAuth.current
    var apiKey by remember { mutableStateOf("") }
    var apiSecret by remember { mutableStateOf("") }
    var keyMode by remember { mutableStateOf("demo") }
    var pairingCode by remember { mutableStateOf<String?>(null) }
    var confirmLive by remember { mutableStateOf(false) }
    var phrase by remember { mutableStateOf("") }
    Screen("Owner Administration", nav, vm.messages, actions = { RefreshIcon { vm.refresh(); dev.refresh() } }) {
        item {
            LoadBox(st, { vm.refresh() }) { s ->
                Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    MxCard {
                        SectionTitle("Trading mode")
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            listOf("paper", "demo", "live").forEach { m ->
                                FilterChip(s.str("mode") == m, {
                                    if (m == "live") confirmLive = true
                                    else auth("Switch to $m") { vm.action("PUT", "/api/settings", jsonBody("mode" to m), "Mode: $m") }
                                }, { Text(m.uppercase()) })
                            }
                        }
                        Text("Endpoint: ${s.str("rest_base")}", color = MX.Muted, style = MaterialTheme.typography.bodySmall)
                        SectionTitle("Order approval")
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            listOf("manual", "auto").forEach { m ->
                                FilterChip(s.str("execution") == m, {
                                    auth("Set $m execution") { vm.action("PUT", "/api/settings", jsonBody("execution" to m), "Execution: $m") }
                                }, { Text(if (m == "manual") "MANUAL APPROVAL" else "FULLY AUTOMATIC") })
                            }
                        }
                        SectionTitle("Capital mode")
                        Row(Modifier.horizontalScroll(androidx.compose.foundation.rememberScrollState()),
                            horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            listOf("pro" to "500 EUR", "multi" to "40 EUR · 10 bots", "experimental" to "4 EUR").forEach { (m, l) ->
                                FilterChip(s.str("capital_mode") == m, {
                                    auth("Set capital mode") { vm.action("PUT", "/api/settings", jsonBody("capital_mode" to m), "Capital: $l") }
                                }, { Text(l) })
                            }
                        }
                        SectionTitle("Strategy timeframe (all bots)")
                        Row(Modifier.horizontalScroll(androidx.compose.foundation.rememberScrollState()),
                            horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            listOf(0 to "Bot default", 30 to "30 min", 60 to "1 h", 240 to "4 h", 15 to "15 min", 5 to "5 min")
                                .forEach { (tf, l) ->
                                    FilterChip(s.num("timeframe").toInt() == tf, {
                                        auth("Set timeframe") {
                                            vm.action("PUT", "/api/settings", jsonBody("timeframe" to tf), "Timeframe: $l — engine restarted")
                                        }
                                    }, { Text(l) })
                                }
                        }
                        Text("Changing it restarts the engine and reloads history (4 h needs ~15 days of 1-minute candles; " +
                                "first start takes longer). Test each in Quant Lab with the same timeframe.",
                            color = MX.Muted, style = MaterialTheme.typography.bodySmall)
                        KV("AI brains (LLM)", if (s.bool("llm_available")) "connected" else "offline — deterministic only")
                        KV("Push notifications (server)", if (s.bool("push_enabled")) "configured" else "not configured")
                        KV("Brain cycle (min)", s.num("brain_interval_min").toInt().toString())
                    }
                    MxCard {
                        SectionTitle("Binance API keys")
                        Banner("Create keys WITHOUT withdrawal permission and restrict them to your server IP. " +
                                "Keys with withdrawals enabled are refused. Keys go to your server, never stay on the phone.", MX.Warn)
                        KV("Stored", s["keys_stored"].keys().joinToString().ifEmpty { "none" })
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            FilterChip(keyMode == "demo", { keyMode = "demo" }, { Text("DEMO") })
                            FilterChip(keyMode == "live", { keyMode = "live" }, { Text("LIVE") })
                        }
                        OutlinedTextField(apiKey, { apiKey = it }, label = { Text("API key") }, singleLine = true,
                            modifier = Modifier.fillMaxWidth())
                        OutlinedTextField(apiSecret, { apiSecret = it }, label = { Text("API secret") }, singleLine = true,
                            visualTransformation = PasswordVisualTransformation(),
                            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                            modifier = Modifier.fillMaxWidth())
                        Button(enabled = apiKey.isNotBlank() && apiSecret.isNotBlank(), onClick = {
                            auth("Store API keys") {
                                vm.action("POST", "/api/settings/apikeys",
                                    jsonBody("mode" to keyMode, "api_key" to apiKey.trim(), "api_secret" to apiSecret.trim()),
                                    "Keys verified and stored encrypted") { apiKey = ""; apiSecret = "" }
                            }
                        }) { Text("Verify & store") }
                    }
                    MxCard {
                        SectionTitle("Live trading gate")
                        val gate = s["live_gate"]
                        KV("Authorisation", if (gate.bool("valid")) "VALID until ${fmtTime(gate.num("expires").toLong())}"
                           else "NOT VALID — ${gate.str("reason")}", if (gate.bool("valid")) MX.Down else MX.Muted)
                        Text("Step 1 — Demo preflight (execution mechanics on Binance DEMO). Does NOT make live ready.",
                            style = MaterialTheme.typography.bodySmall)
                        val pf = s["preflight"]
                        KV("Demo preflight", when { pf == null -> "never run"; pf.bool("ok") -> "PASSED ${fmtTime(pf.num("ts").toLong())}"
                            else -> "FAILED" }, if (pf.bool("ok")) MX.Up else MX.Down)
                        pf["checks"].keys().forEach { k ->
                            if (!pf["checks"][k].bool("pass")) KV(k, pf["checks"][k].str("detail").take(60), MX.Down)
                        }
                        OutlinedButton(onClick = { auth("Run demo preflight") {
                            vm.action("POST", "/api/preflight", "{}", "Demo preflight finished") } }) { Text("Run demo preflight") }
                        Text("Step 2 — Live key & account check (Futures on, withdrawals off, IP-restricted, equity, margin).",
                            style = MaterialTheme.typography.bodySmall)
                        val lc = s["live_check"]
                        KV("Live check", when { lc == null -> "never run"; lc.bool("ok") -> "PASSED ${fmtTime(lc.num("ts").toLong())}"
                            else -> "FAILED" }, if (lc.bool("ok")) MX.Up else MX.Down)
                        lc["checks"].keys().forEach { k ->
                            KV(k, lc["checks"][k].str("detail").take(50), if (lc["checks"][k].bool("pass")) MX.Up else MX.Down)
                        }
                        OutlinedButton(onClick = { auth("Check live key and account") {
                            vm.action("POST", "/api/live-check", "{}", "Live check finished") } }) { Text("Run live key check") }
                        Text("Step 3 — Owner confirmation. Valid for a limited time; any key rotation or risk/config change " +
                                "invalidates it.", style = MaterialTheme.typography.bodySmall)
                        OutlinedTextField(phrase, { phrase = it }, label = { Text("Type: I UNDERSTAND THE RISK") },
                            singleLine = true, modifier = Modifier.fillMaxWidth())
                        Button(enabled = phrase == "I UNDERSTAND THE RISK", onClick = {
                            auth("Unlock live trading") {
                                vm.action("POST", "/api/unlock-live", jsonBody("confirm" to phrase), "Live authorisation granted")
                            }
                        }) { Text("Authorise live trading") }
                        Text("Only bots that are LIVE_ELIGIBLE (walk-forward + Monte Carlo + paper evidence) can trade live. " +
                                "Default and fallback mode is PAPER.", color = MX.Muted, style = MaterialTheme.typography.bodySmall)
                    }
                }
            }
        }
        item {
            val ctx = androidx.compose.ui.platform.LocalContext.current
            var enabled by remember { mutableStateOf(com.millionx.ai.engine.LocalEngine.isEnabled(ctx)) }
            MxCard {
                SectionTitle("Engine on this phone")
                KV("Local engine", if (enabled) "ON (background service)" else "OFF", if (enabled) MX.Up else MX.Muted)
                KV("Battery optimisation", if (com.millionx.ai.engine.LocalEngine.ignoringBatteryOptimizations(ctx))
                    "disabled for this app ✓" else "ACTIVE — Android may stop the engine",
                    if (com.millionx.ai.engine.LocalEngine.ignoringBatteryOptimizations(ctx)) MX.Up else MX.Warn)
                Text("Samsung: Settings → Apps → MILLION-X → Battery → Unrestricted, and remove it from 'Sleeping apps'.",
                    color = MX.Muted, style = MaterialTheme.typography.bodySmall)
                val problem = com.millionx.ai.engine.LocalEngine.lastProblem
                if (problem.isNotEmpty()) Text(problem, color = MX.Warn, style = MaterialTheme.typography.bodySmall)
                var showLog by remember { mutableStateOf(false) }
                if (showLog) EngineLogDialog { showLog = false }
                TextButton(onClick = { showLog = true }) { Text("Show engine log") }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedButton(onClick = { com.millionx.ai.engine.LocalEngine.start(ctx); enabled = true }) { Text("Start") }
                    OutlinedButton(onClick = { auth("Stop local engine") {
                        com.millionx.ai.engine.LocalEngine.stop(ctx); enabled = false } }) { Text("Stop", color = MX.Down) }
                    OutlinedButton(onClick = { runCatching {
                        ctx.startActivity(com.millionx.ai.engine.LocalEngine.batteryOptimizationIntent(ctx)) } }) { Text("Battery") }
                }
            }
        }
        item {
            MxCard {
                SectionTitle("Devices & roles")
                LoadBox(ds, { dev.refresh() }) { list ->
                    Column {
                        list.arr.forEach { d ->
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Column(Modifier.weight(1f)) {
                                    Text(d.str("name"), fontWeight = FontWeight.SemiBold)
                                    Text("${d.str("role")} · last seen ${fmtTime(d.num("last_seen").toLong())}", color = MX.Muted,
                                        style = MaterialTheme.typography.bodySmall)
                                }
                                if (d.bool("revoked")) Chip("REVOKED", MX.Down) else TextButton(onClick = {
                                    auth("Revoke device") { dev.action("POST", "/api/admin/devices/${d.str("id")}/revoke", "{}", "Revoked") }
                                }) { Text("Revoke", color = MX.Down) }
                            }
                        }
                    }
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedButton(onClick = { auth("Create viewer code") {
                        dev.action("POST", "/api/admin/pairing-code", jsonBody("role" to "viewer"), "Code created") { pairingCode = it.str("code") } } }) {
                        Text("Viewer code")
                    }
                    OutlinedButton(onClick = { auth("Create owner code") {
                        dev.action("POST", "/api/admin/pairing-code", jsonBody("role" to "owner"), "Code created") { pairingCode = it.str("code") } } }) {
                        Text("Owner code")
                    }
                }
                pairingCode?.let { Text("Pairing code (10 min): $it", style = MaterialTheme.typography.titleMedium, color = MX.Accent) }
                TextButton(onClick = onLogout) { Text("Unpair this phone", color = MX.Down) }
            }
        }
    }
    if (confirmLive) ConfirmDialog(
        "Switch to LIVE",
        "Real money. Requires passed preflight and owner unlock. Only validated bots will trade. Continue?",
        "Switch to live",
        { auth("Switch to LIVE") { vm.action("PUT", "/api/settings", jsonBody("mode" to "live"), "LIVE mode") } },
        { confirmLive = false },
    )
}

data class Module(val route: String, val title: String, val subtitle: String, val icon: ImageVector)

val MODULES = listOf(
    Module("dashboard", "Main Dashboard", "Capital, P/L, goal", Icons.Filled.Insights),
    Module("live", "Live Trading Floor", "Charts, signals, why", Icons.Filled.Speed),
    Module("exchange", "Binance Account", "Balance, real P/L", Icons.Filled.AccountBalance),
    Module("ai_center", "AI Command Center", "Trader, Supervisor, providers", Icons.Filled.Psychology),
    Module("perf_audit", "Performance Auditor", "Real vs simulated", Icons.Filled.Insights),
    Module("research2", "Research 2.0", "Edge, selection, NO TRADE", Icons.Filled.Science),
    Module("boardroom", "AI Boardroom", "5 executives", Icons.Filled.Groups),
    Module("brains", "Six-Brain Control", "Prompts, logs, cost", Icons.Filled.Psychology),
    Module("bots", "Ten-Bot Trading Floor", "Status & stats", Icons.Filled.SmartToy),
    Module("risk", "Risk Management", "Limits, emergency", Icons.Filled.Shield),
    Module("portfolio", "Portfolio & Live", "Positions, weights", Icons.Filled.AccountBalance),
    Module("quant", "Quant Research Lab", "Backtest, WF, MC", Icons.Filled.Science),
    Module("execution", "Execution & Scanner", "Orders, market AI", Icons.Filled.Speed),
    Module("audit", "Audit & Compliance", "Hash-chained log", Icons.Filled.Gavel),
    Module("admin", "Owner Administration", "Modes, keys, devices", Icons.Filled.AdminPanelSettings),
    Module("strategies", "Strategy Center", "Compare by regime", Icons.Filled.Radar),
    Module("trades", "Trade History", "Real & shadow", Icons.Filled.History),
    Module("notifications", "Notifications", "Alerts & prefs", Icons.Filled.Notifications),
)

@Composable
fun MoreScreen(nav: NavController) {
    Screen("Command Center", nav) {
        item {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                MODULES.chunked(2).forEach { row ->
                    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        row.forEach { m ->
                            Box(Modifier.weight(1f)) {
                                MxCard(onClick = { nav.navigate(m.route) }) {
                                    Icon(m.icon, null, tint = MX.Accent)
                                    Text(m.title, fontWeight = FontWeight.SemiBold)
                                    Text(m.subtitle, color = MX.Muted, style = MaterialTheme.typography.bodySmall)
                                }
                            }
                        }
                        if (row.size == 1) Spacer(Modifier.weight(1f))
                    }
                }
            }
        }
    }
}

/** Last lines of the embedded engine's server.log, so start problems can be read on the phone. */
@Composable
fun EngineLogDialog(onClose: () -> Unit) {
    val ctx = androidx.compose.ui.platform.LocalContext.current
    var text by remember { mutableStateOf("Loading…") }
    var tick by remember { mutableStateOf(0) }
    LaunchedEffect(tick) {
        text = kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) {
            val e = com.millionx.ai.engine.LocalEngine
            buildString {
                append("Local URL: ${e.URL}\n")
                if (e.lastProblem.isNotEmpty()) append("Problem: ${e.lastProblem}\n")
                runCatching { e.pythonError() }.getOrNull()?.takeIf { it.isNotEmpty() }?.let { append("Python: $it\n") }
                append("\n").append(e.logTail(ctx))
            }
        }
    }
    androidx.compose.material3.AlertDialog(
        onDismissRequest = onClose,
        confirmButton = { TextButton(onClick = onClose) { Text("Close") } },
        dismissButton = { TextButton(onClick = { tick++ }) { Text("Refresh") } },
        title = { Text("Engine log") },
        text = {
            Column(Modifier.height(420.dp).verticalScroll(androidx.compose.foundation.rememberScrollState())) {
                androidx.compose.foundation.text.selection.SelectionContainer {
                    Text(text, style = MaterialTheme.typography.bodySmall,
                        fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace)
                }
            }
        },
    )
}

/** First run: connect the phone to the backend with a one-time code. */
@Composable
fun PairingScreen(onPaired: () -> Unit) {
    val app = MillionXApp.instance
    val scope = rememberCoroutineScope()
    var server by remember { mutableStateOf(app.store.serverUrl ?: "https://") }
    var code by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    Column(Modifier.fillMaxSize().padding(24.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
        Spacer(Modifier.height(48.dp))
        Text("MILLION-X AI", style = MaterialTheme.typography.headlineLarge, color = MX.Accent)
        Text("Run the trading engine on this phone (free), or connect to your own server.", color = MX.Muted)
        val ctx = androidx.compose.ui.platform.LocalContext.current
        var localStatus by remember { mutableStateOf<String?>(null) }
        Button(enabled = !busy, modifier = Modifier.fillMaxWidth().height(56.dp).testTag("start_local"), onClick = {
            busy = true; error = null
            localStatus = "Starting the engine on this phone… first start takes 1–3 minutes"
            scope.launch {
                val err = com.millionx.ai.engine.LocalEngine.startAndPair(ctx) { st -> localStatus = st }
                busy = false
                if (err == null) {
                    if (!com.millionx.ai.engine.LocalEngine.ignoringBatteryOptimizations(ctx)) {
                        runCatching { ctx.startActivity(com.millionx.ai.engine.LocalEngine.batteryOptimizationIntent(ctx)) }
                    }
                    onPaired()
                } else { error = err; localStatus = null }
            }
        }) { Text("▶  Run engine on this phone (free)") }
        localStatus?.let { Text(it, color = MX.Info, style = MaterialTheme.typography.bodySmall) }
        var showLog by remember { mutableStateOf(false) }
        if (error != null || busy) TextButton(onClick = { showLog = true }) { Text("Show engine log") }
        if (showLog) EngineLogDialog { showLog = false }
        Text("— or connect to a server —", color = MX.Muted, style = MaterialTheme.typography.bodySmall)
        OutlinedTextField(server, { server = it }, label = { Text("Server URL (https://…)") }, singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri), modifier = Modifier.fillMaxWidth())
        OutlinedTextField(code, { code = it.uppercase() }, label = { Text("Pairing code") }, singleLine = true,
            modifier = Modifier.fillMaxWidth())
        Text("On the server run: python -m millionx.cli pair", color = MX.Muted, style = MaterialTheme.typography.bodySmall)
        error?.let { Text(it, color = MX.Down) }
        Button(enabled = !busy && code.length >= 6, modifier = Modifier.fillMaxWidth().height(52.dp), onClick = {
            busy = true; error = null
            scope.launch {
                try {
                    app.api.pair(server.trim(), code.trim(), "${Build.MANUFACTURER} ${Build.MODEL}")
                    onPaired()
                } catch (e: Exception) {
                    error = e.message ?: "Pairing failed"
                } finally { busy = false }
            }
        }) { Text(if (busy) "Connecting…" else "Pair device") }
        Text("Local mode keeps running in the background (notification \"MILLION-X engine\"), but stops if the phone is " +
                "off or offline. This app controls a trading engine. Trading leveraged futures can lose all deposited capital. " +
                "The 1M EUR goal is an experiment, not a promise.", color = MX.Muted, style = MaterialTheme.typography.bodySmall)
    }
}

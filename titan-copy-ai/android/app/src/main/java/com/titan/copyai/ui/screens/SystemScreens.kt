package com.titan.copyai.ui.screens

import android.Manifest
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.titan.copyai.ui.components.Chip
import com.titan.copyai.ui.components.Empty
import com.titan.copyai.ui.components.Fmt
import com.titan.copyai.ui.components.KV
import com.titan.copyai.ui.components.Panel
import com.titan.copyai.ui.components.at
import com.titan.copyai.ui.components.b
import com.titan.copyai.ui.components.s
import com.titan.copyai.ui.nav.ContentPadding
import com.titan.copyai.ui.nav.LocalCtx
import com.titan.copyai.ui.theme.Titan
import com.titan.copyai.ui.vm.live
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

@Composable
fun NotificationsScreen() {
    val ctx = LocalCtx.current
    val repo = ctx.app.repo
    val items by repo.db.notifications().observe().collectAsState(initial = emptyList())
    val scope = rememberCoroutineScope()
    LaunchedEffect(ctx.tick) { runCatching { repo.syncNotifications() } }
    LazyColumn(contentPadding = ContentPadding, verticalArrangement = Arrangement.spacedBy(8.dp)) {
        item {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton({ scope.launch { runCatching { repo.syncNotifications() }.onFailure { ctx.snack(it.message ?: "sync failed") } } }) {
                    Text("Refresh")
                }
                OutlinedButton({
                    scope.launch {
                        val ids = items.filter { !it.read }.map { it.id }
                        if (ids.isNotEmpty()) {
                            runCatching { repo.api.markRead(buildJsonObject { put("ids", JsonArray(ids.map { JsonPrimitive(it) })) }) }
                            repo.db.notifications().markRead(ids)
                        }
                    }
                }) { Text("Mark all read") }
            }
        }
        if (items.isEmpty()) item { Empty("No notifications") }
        items(items, key = { it.id }) { n ->
            Panel {
                Row {
                    Chip(n.severity, when (n.severity) { "critical" -> Titan.Down; "warning" -> Titan.Warn; else -> Titan.Accent })
                    Spacer(Modifier.width(8.dp))
                    Text(Fmt.time(n.ts), color = Titan.Muted, style = MaterialTheme.typography.bodySmall, modifier = Modifier.weight(1f))
                    if (!n.read) Chip("new", Titan.Gold)
                }
                Text(n.title, style = MaterialTheme.typography.titleMedium)
                if (n.body.isNotBlank()) Text(n.body, style = MaterialTheme.typography.bodyMedium, color = Titan.Muted)
            }
        }
    }
}

@Composable
fun SettingsScreen() {
    val ctx = LocalCtx.current
    val vm = rememberLoad("settings") { live(Pair(api.settings(), api.readiness())) }
    val perm = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { }
    LoadedList(vm) { (s, ready) ->
        if (ctx.app.repo.store.localMode) item { EnginePanel() }
        item {
            Panel(title = if (ctx.app.repo.store.localMode) "Engine configuration" else "Server") {
                KV("URL", ctx.app.repo.store.serverUrl)
                KV("Environment", s.at("env").s() ?: "")
                KV("Live trading on server", if (s.at("live_trading_enabled").b() == true) "ENABLED" else "DISABLED",
                    if (s.at("live_trading_enabled").b() == true) Titan.Warn else Titan.Up)
                KV("AI (Claude) available", if (s.at("llm_available").b() == true) "yes · ${s.at("llm_model").s()}" else "no (deterministic only)")
                KV("Max signal age forex / crypto", "${s.at("max_signal_age_forex_s").s()} s / ${s.at("max_signal_age_crypto_s").s()} s")
                KV("Push (FCM)", if (s.at("fcm_configured").b() == true) "configured" else "not configured (live socket + 15-min sync)")
            }
        }
        item {
            Panel(title = "Live trading gate") {
                ready.forEach { r ->
                    Text("${r.at("portfolio").s()} · ${if (r.at("ready").b() == true) "READY" else "NOT READY"}",
                        style = MaterialTheme.typography.titleMedium, color = if (r.at("ready").b() == true) Titan.Up else Titan.Warn)
                    (r["checks"] as? JsonArray)?.forEach { c ->
                        val o = c as kotlinx.serialization.json.JsonObject
                        KV((if (o.at("ok").b() == true) "✓ " else "✕ ") + (o.at("check").s() ?: ""), o.at("value")?.toString() ?: "",
                            if (o.at("ok").b() == true) Titan.Up else Titan.Down)
                    }
                    Spacer(Modifier.height(8.dp))
                }
                Text("At least 30 days of paper trading are required before live trading can even be considered. " +
                    "No result is guaranteed.", style = MaterialTheme.typography.bodySmall, color = Titan.Muted)
            }
        }
        item {
            Panel(title = "Device") {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (Build.VERSION.SDK_INT >= 33) {
                        OutlinedButton({ perm.launch(Manifest.permission.POST_NOTIFICATIONS) }, modifier = Modifier.fillMaxWidth()) {
                            Text("Allow notifications")
                        }
                    }
                    Button({ ctx.app.logout() }, modifier = Modifier.fillMaxWidth()) { Text("Unpair this device") }
                }
            }
        }
    }
}

@Composable
fun AuditScreen() {
    val vm = rememberLoad("audit") { live(Pair(api.auditVerify(), api.audit(300))) }
    LoadedList(vm) { (verify, rows) ->
        item {
            Panel(title = "Tamper-evident hash chain") {
                val ok = verify.at("valid").b() == true
                Text(if (ok) "INTACT · ${verify.at("checked").s()} entries" else "BROKEN at entry ${verify.at("broken_at_id").s()}",
                    color = if (ok) Titan.Up else Titan.Down, style = MaterialTheme.typography.titleMedium)
            }
        }
        if (rows.isEmpty()) item { Empty("No audit entries") }
        items(rows, key = { it.id }) { a ->
            Panel {
                Row {
                    Text(a.action, style = MaterialTheme.typography.titleSmall, modifier = Modifier.weight(1f))
                    Text(Fmt.time(a.ts), color = Titan.Muted, style = MaterialTheme.typography.bodySmall)
                }
                Text("${a.actor} · ${a.entity ?: "-"} · #${a.hash}", color = Titan.Muted, style = MaterialTheme.typography.bodySmall)
                if (a.detail.isNotEmpty()) Text(a.detail.toString().take(400), style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}


/** On-phone engine controls: status, battery exemption, restart/stop, log, Claude key. */
@Composable
fun EnginePanel() {
    val ctx = LocalCtx.current
    val context = androidx.compose.ui.platform.LocalContext.current
    val scope = rememberCoroutineScope()
    var status by androidx.compose.runtime.remember { androidx.compose.runtime.mutableStateOf("checking…") }
    var ok by androidx.compose.runtime.remember { androidx.compose.runtime.mutableStateOf<Boolean?>(null) }
    var log by androidx.compose.runtime.remember { androidx.compose.runtime.mutableStateOf<String?>(null) }
    var key by androidx.compose.runtime.remember { androidx.compose.runtime.mutableStateOf("") }
    var battery by androidx.compose.runtime.remember {
        androidx.compose.runtime.mutableStateOf(com.titan.copyai.engine.LocalEngine.ignoringBatteryOptimizations(context))
    }
    LaunchedEffect(ctx.tick) {
        val r = kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) { com.titan.copyai.engine.LocalEngine.probe(context) }
        ok = r.first; status = r.second
        battery = com.titan.copyai.engine.LocalEngine.ignoringBatteryOptimizations(context)
    }
    Panel(title = "Engine on this phone") {
        com.titan.copyai.ui.components.StatusDot(ok, if (ok == true) "Running · 127.0.0.1:${com.titan.copyai.engine.LocalEngine.port}" else status)
        Spacer(Modifier.height(6.dp))
        Text("Runs 24/7 in a foreground service (survives closing the app, screen off and reboots). It stops if the " +
            "phone is off or has no internet; open positions keep their stop-loss on the broker/exchange.",
            style = MaterialTheme.typography.bodySmall, color = Titan.Muted)
        Spacer(Modifier.height(8.dp))
        if (!battery) {
            Button({ context.startActivity(com.titan.copyai.engine.LocalEngine.batteryOptimizationIntent(context)) },
                modifier = Modifier.fillMaxWidth()) { Text("Allow running in background (battery)") }
            Text("Required on most phones (Samsung, Xiaomi…) or Android may pause the engine.",
                color = Titan.Warn, style = MaterialTheme.typography.bodySmall)
        } else KV("Battery optimisation", "exempt", Titan.Up)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton({ com.titan.copyai.engine.LocalEngine.start(context); ctx.snack("Engine (re)started") }) { Text("Start") }
            OutlinedButton({ com.titan.copyai.engine.LocalEngine.stop(context); ctx.snack("Engine stopped - no copying") }) { Text("Stop") }
            OutlinedButton({
                scope.launch {
                    log = kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) {
                        com.titan.copyai.engine.LocalEngine.logTail(context)
                    }
                }
            }) { Text("Log") }
        }
        log?.let { Text(it.takeLast(6000), style = MaterialTheme.typography.bodySmall, color = Titan.Muted) }
        Spacer(Modifier.height(8.dp))
        androidx.compose.material3.OutlinedTextField(key, { key = it.trim() }, singleLine = true,
            label = { Text(if (ctx.app.repo.store.anthropicKey.isBlank()) "Claude API key (optional)" else "Claude API key: set (enter to replace)") },
            visualTransformation = androidx.compose.ui.text.input.PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth())
        OutlinedButton({
            ctx.app.repo.store.anthropicKey = key; key = ""
            com.titan.copyai.engine.LocalEngine.stop(context)
            scope.launch { kotlinx.coroutines.delay(1500); com.titan.copyai.engine.LocalEngine.start(context) }
            ctx.snack("Saved (encrypted). Engine restarting.")
        }, enabled = key.length > 20) { Text("Save key & restart engine") }
    }
}

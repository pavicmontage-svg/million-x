package com.titan.copyai.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import com.titan.copyai.data.api.ConnectionDto
import com.titan.copyai.data.api.CredentialIn
import com.titan.copyai.ui.components.Chip
import com.titan.copyai.ui.components.Empty
import com.titan.copyai.ui.components.Fmt
import com.titan.copyai.ui.components.KV
import com.titan.copyai.ui.components.Panel
import com.titan.copyai.ui.components.at
import com.titan.copyai.ui.components.b
import com.titan.copyai.ui.components.s
import com.titan.copyai.ui.nav.LocalCtx
import com.titan.copyai.ui.theme.Titan
import com.titan.copyai.ui.vm.LoadVm
import com.titan.copyai.ui.vm.live
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

private data class VenueSpec(val venue: String, val title: String, val fields: List<Pair<String, Boolean>>, val note: String)

private val BROKERS = listOf(
    VenueSpec("ctrader", "cTrader Open API (execution / source)", listOf("client_id" to false, "client_secret" to true,
        "access_token" to true, "account_id" to false),
        "Register an Open API app at openapi.ctrader.com, grant 'trading' scope to your account via id.ctrader.com OAuth. " +
            "JSON over WebSocket on demo/live.ctraderapi.com:5036."),
    VenueSpec("mt5", "MetaTrader 5 (self-hosted bridge)", listOf("url" to false, "secret" to true),
        "Run bridges/mt5_bridge.py next to your logged-in MT5 terminal (Windows). Your MT5 password never leaves that machine."),
    VenueSpec("myfxbook", "Myfxbook API (monitoring)", listOf("email" to false, "password" to true),
        "Official Myfxbook API: your own and watched accounts. Monitoring only - Myfxbook AutoTrade has no public API."),
    VenueSpec("darwinex", "Darwinex DARWIN Info API (monitoring)", listOf("token" to true),
        "Data-API token from your Darwinex account (valid ~6 months). DARWIN quotes are risk-normalised indices."),
)
private val BINANCE = listOf(
    VenueSpec("binance_usdm", "Binance USDⓈ-M Futures", listOf("api_key" to false, "api_secret" to true),
        "Create keys at demo.binance.com (Demo Trading). Enable futures trading only. Never enable withdrawals."),
    VenueSpec("binance_spot", "Binance Spot", listOf("api_key" to false, "api_secret" to true),
        "Spot demo keys from demo.binance.com. Long-only copying with OCO protection."),
    VenueSpec("binance_coinm", "Binance COIN-M Futures", listOf("api_key" to false, "api_secret" to true),
        "Inverse contracts margined in coin. Separate demo keys."),
)

@Composable
fun BrokerConnectionsScreen() = ConnectionsScreen("brokers", BROKERS, "Forex broker & data connections",
    "Integrations that do NOT exist and are therefore monitoring-only via imported history: ZuluTrade (no public API), " +
        "MQL5 Signals (copying only inside MT4/MT5), cTrader Copy strategy listings (no listing endpoint).")

@Composable
fun BinanceConnectionsScreen() = ConnectionsScreen("binance", BINANCE, "Binance API connections",
    "Demo Trading by default. Live keys are refused unless the server has live trading enabled AND the paper-trading gate " +
        "is passed AND you type an approval. Note: since 1 July 2026 Binance does not accept new orders from EEA residents " +
        "(MiCA) - live Binance trading from Croatia is likely unavailable; Demo remains usable for testing.")

@Composable
private fun ConnectionsScreen(key: String, specs: List<VenueSpec>, title: String, note: String) {
    val ctx = LocalCtx.current
    val venues = specs.map { it.venue }.toSet()
    val vm = rememberLoad("conn-$key") { live(api.connections().filter { it.venue in venues }) }
    LoadedList(vm) { list ->
        item { Text(note, style = MaterialTheme.typography.bodySmall, color = Titan.Muted) }
        items(list, key = { it.id }) { c -> ConnectionCard(c, vm) }
        if (list.isEmpty()) item { Empty("No $title yet") }
        item { AddConnection(specs, vm) }
        item {
            Text("Keys are sent once over TLS, encrypted on the server with AES-256-GCM and never shown again. " +
                "The phone stores only its own device token.", style = MaterialTheme.typography.bodySmall, color = Titan.Muted)
        }
    }
    @Suppress("UNUSED_VARIABLE") val unused = ctx
}

@Composable
private fun ConnectionCard(c: ConnectionDto, vm: LoadVm<List<ConnectionDto>>) {
    val ctx = LocalCtx.current
    val ok = c.verifyResult.at("ok").b()
    Panel(title = "${c.venue} · ${c.environment}", trailing = {
        Chip(when (ok) { true -> "verified"; false -> "failed"; null -> "unverified" },
            when (ok) { true -> Titan.Up; false -> Titan.Down; null -> Titan.Muted })
    }) {
        Text(c.label, style = MaterialTheme.typography.titleMedium)
        KV("Key fingerprint", c.fingerprint)
        KV("Verified", Fmt.time(c.verifiedAt))
        c.verifyResult.at("error").s()?.let { Text(it, color = Titan.Down, style = MaterialTheme.typography.bodySmall) }
        c.verifyResult.at("warning").s()?.let { Text(it, color = Titan.Warn, style = MaterialTheme.typography.bodySmall) }
        c.verifyResult.at("equity").s()?.let { KV("Account equity", it) }
        KV("Used by", c.portfolios.joinToString().ifBlank { "—" })
        Spacer(Modifier.height(6.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton({ vm.action("Verification queued") { ctx.app.repo.api.verifyConnection(c.id) } }) { Text("Verify") }
            if (c.portfolios.isEmpty() && (c.venue.startsWith("binance") || c.venue in setOf("ctrader", "mt5"))) {
                OutlinedButton({
                    vm.action("Portfolio created (copying OFF until you enable it)") {
                        ctx.app.repo.api.attachPortfolio(c.id, buildJsonObject {
                            put("credential_id", c.id)
                            put("market", if (c.venue.startsWith("binance")) "crypto" else "forex")
                            put("name", "${c.label} ${c.environment}")
                            put("venue", c.venue)
                            put("capital", 500.0)
                        })
                    }
                }) { Text("Create portfolio") }
            }
            if (c.portfolios.isEmpty()) OutlinedButton({ vm.action("Deleted") { ctx.app.repo.api.deleteConnection(c.id) } }) { Text("Delete") }
        }
    }
}

@Composable
private fun AddConnection(specs: List<VenueSpec>, vm: LoadVm<List<ConnectionDto>>) {
    val ctx = LocalCtx.current
    var spec by remember { mutableStateOf(specs.first()) }
    var env by remember { mutableStateOf("demo") }
    var label by remember { mutableStateOf("") }
    val values = remember(spec) { mutableStateMapOf<String, String>() }
    Panel(title = "Add connection") {
        specs.forEach { s -> FilterChip(selected = spec == s, onClick = { spec = s }, label = { Text(s.title) }) }
        Text(spec.note, style = MaterialTheme.typography.bodySmall, color = Titan.Muted)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf("demo", "live").forEach { e -> FilterChip(selected = env == e, onClick = { env = e }, label = { Text(e.uppercase()) }) }
        }
        OutlinedTextField(label, { label = it }, label = { Text("Label") }, singleLine = true, modifier = Modifier.fillMaxWidth())
        spec.fields.forEach { (f, secret) ->
            OutlinedTextField(values[f] ?: "", { values[f] = it.trim() }, label = { Text(f) }, singleLine = true,
                visualTransformation = if (secret) PasswordVisualTransformation() else androidx.compose.ui.text.input.VisualTransformation.None,
                keyboardOptions = KeyboardOptions(keyboardType = if (secret) KeyboardType.Password else KeyboardType.Text),
                modifier = Modifier.fillMaxWidth())
        }
        Spacer(Modifier.height(8.dp))
        Button({
            val fields = spec.fields.associate { (f, _) -> f to (values[f] ?: "") }
            vm.action("Saved and encrypted on the server") {
                ctx.app.repo.api.addConnection(CredentialIn(spec.venue, env, label.ifBlank { spec.venue }, fields))
                values.clear()
            }
        }, enabled = spec.fields.all { (f, _) -> !values[f].isNullOrBlank() }) { Text("Save securely") }
    }
}

@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package com.millionx.ai.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
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
import com.millionx.ai.data.arr
import com.millionx.ai.data.bool
import com.millionx.ai.data.fmt
import com.millionx.ai.data.get
import com.millionx.ai.data.jsonBody
import com.millionx.ai.data.list
import com.millionx.ai.data.long
import com.millionx.ai.data.num
import com.millionx.ai.data.str
import com.millionx.ai.ui.components.Banner
import com.millionx.ai.ui.components.BarList
import com.millionx.ai.ui.components.Chip
import com.millionx.ai.ui.components.KV
import com.millionx.ai.ui.components.LineChart
import com.millionx.ai.ui.components.LoadBox
import com.millionx.ai.ui.components.LocalAuth
import com.millionx.ai.ui.components.MxCard
import com.millionx.ai.ui.components.RefreshIcon
import com.millionx.ai.ui.components.Screen
import com.millionx.ai.ui.components.SectionTitle
import com.millionx.ai.ui.components.Stat
import com.millionx.ai.ui.components.rememberResource
import com.millionx.ai.ui.theme.MX
import com.millionx.ai.ui.theme.Mono
import com.millionx.ai.ui.theme.pnlColor
import kotlinx.serialization.json.JsonElement

private fun usd(v: Double, d: Int = 2) = "${if (v > 0) "+" else ""}${v.fmt(d)} $"

/** BINANCE ACCOUNT: read-only balance, positions and real P/L straight from Binance. */
@Composable
fun ExchangeScreen(nav: NavController) {
    var account by rememberSaveable { mutableStateOf("") }
    var days by rememberSaveable { mutableStateOf(7) }
    val q = (if (account.isNotEmpty()) "account=$account&" else "") + "days=$days"
    val vm = rememberResource("/api/exchange/account?$q", 20_000, setOf("trade", "position_opened"))
    val st by vm.state.collectAsState()
    Screen("Binance account", nav, vm.messages, actions = { RefreshIcon { vm.refresh() } }) {
        item {
            LoadBox(st, { vm.refresh() }) { d ->
                Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                        listOf("demo", "live").forEach { a ->
                            FilterChip(d.str("account") == a, { account = a },
                                { Text(a.uppercase() + if (d["stored"].bool(a)) " ✓" else "") })
                        }
                        Text("  ")
                        listOf(1, 7, 30).forEach { n -> FilterChip(days == n, { days = n }, { Text("${n}d") }) }
                    }
                    if (!d.bool("connected")) ConnectCard(vm, d.str("account"))
                    else AccountBody(vm, d)
                }
            }
        }
    }
}

@Composable
private fun ConnectCard(vm: com.millionx.ai.ui.vm.ResourceVM, which: String) {
    val auth = LocalAuth.current
    var key by remember { mutableStateOf("") }
    var secret by remember { mutableStateOf("") }
    var msg by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(vm) { vm.messages.collect { msg = it } }
    MxCard {
        SectionTitle("Connect Binance ${which.uppercase()}")
        if (which == "demo") {
            Text("Recommended: Binance Demo Trading — real Binance order book, virtual money, no risk.",
                fontWeight = FontWeight.SemiBold)
            Text("1. In the Binance app: More → Demo Trading (or open demo.binance.com and log in).\n" +
                    "2. Profile icon → Demo Trading API → Create API (system generated).\n" +
                    "3. Copy the API key and the secret key here.",
                style = MaterialTheme.typography.bodySmall)
        } else {
            Banner("LIVE = real money. Live keys must have Futures permission, NO withdrawal permission and an IP " +
                    "restriction — a phone's IP changes, so live keys only make sense on a server with a fixed IP. " +
                    "Live trading also stays locked until preflight + owner unlock.", MX.Down)
        }
        OutlinedTextField(key, { key = it.trim() }, label = { Text("API key") }, singleLine = true,
            modifier = Modifier.fillMaxWidth())
        OutlinedTextField(secret, { secret = it.trim() }, label = { Text("Secret key") }, singleLine = true,
            visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth())
        Button(enabled = key.length >= 20 && secret.length >= 20, onClick = {
            auth("Store Binance ${which.uppercase()} key") {
                vm.action("POST", "/api/settings/apikeys",
                    jsonBody("mode" to which, "api_key" to key, "api_secret" to secret),
                    "Binance ${which.uppercase()} connected") { key = ""; secret = "" }
            }
        }) { Text("Check & connect") }
        msg?.let { Text(it, color = if (it.contains("connected")) MX.Up else MX.Down, style = MaterialTheme.typography.bodySmall) }
        Text("The key is checked with Binance, then stored encrypted on the engine. Withdrawal-enabled keys are refused.",
            color = MX.Muted, style = MaterialTheme.typography.bodySmall)
    }
}

@Composable
private fun AccountBody(vm: com.millionx.ai.ui.vm.ResourceVM, d: JsonElement) {
    val auth = LocalAuth.current
    if (d.str("error").isNotEmpty()) {
        Banner("Binance: ${d.str("error")}", MX.Down)
        return
    }
    val which = d.str("account")
    if (!d.bool("bots_trade_here")) {
        MxCard {
            Text("The bots are NOT trading on this account yet", fontWeight = FontWeight.SemiBold, color = MX.Warn)
            Text("Engine mode is ${d.str("engine_mode").uppercase()}. Numbers below are this Binance account only " +
                    "(including anything you traded by hand).", style = MaterialTheme.typography.bodySmall)
            if (which == "demo") OutlinedButton(onClick = {
                auth("Let the bots trade on Binance DEMO") {
                    vm.action("PUT", "/api/settings", jsonBody("mode" to "demo"), "Engine switched to DEMO")
                }
            }) { Text("Let the bots trade on this DEMO account") }
        }
    }
    val b = d["balance"]
    val p = d["pnl"]
    MxCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Balance", color = MX.Muted, modifier = Modifier.weight(1f))
            Chip(which.uppercase(), if (which == "live") MX.Down else MX.Info)
        }
        Text("${b.num("equity").fmt(2)} USDT", style = MaterialTheme.typography.headlineMedium, fontFamily = Mono)
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Stat("Wallet", b.num("wallet").fmt(2), Modifier.weight(1f))
            Stat("Available", b.num("available").fmt(2), Modifier.weight(1f))
            Stat("Open P/L", usd(b.num("unrealized")), Modifier.weight(1f), pnlColor(b.num("unrealized")))
        }
    }
    MxCard {
        SectionTitle("Profit / loss from Binance — last ${d.num("days").toInt()} days")
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Stat("Net", usd(p.num("net")), Modifier.weight(1f), pnlColor(p.num("net")))
            Stat("Today", usd(p.num("today_net")), Modifier.weight(1f), pnlColor(p.num("today_net")))
            val closes = p.num("closing_fills").toInt()
            Stat("Wins", if (closes > 0) "${p.num("winning_fills").toInt()}/$closes" else "—", Modifier.weight(1f))
        }
        val pts = p.list("curve").map { it.arr[1].toString().toDouble() }
        LineChart(listOf(0.0) + pts, baseline = 0.0, color = if (p.num("net") >= 0) MX.Up else MX.Down)
        KV("Realized P/L", usd(p.num("realized"), 4), pnlColor(p.num("realized")))
        KV("Trading fees", usd(p.num("commission"), 4), pnlColor(p.num("commission")))
        KV("Funding", usd(p.num("funding"), 4), pnlColor(p.num("funding")))
        val daily = p.list("daily")
        if (daily.size > 1) {
            Text("Per day", color = MX.Muted, style = MaterialTheme.typography.bodySmall)
            BarList(daily.takeLast(10).map { fmtTime(it.long("day")).substringBefore(' ') to it.num("net") },
                format = { usd(it) })
        }
        Text("Net = realized P/L + fees + funding, as reported by Binance. Deposits and transfers are not counted as profit.",
            color = MX.Muted, style = MaterialTheme.typography.bodySmall)
    }
    MxCard {
        SectionTitle("Open positions on Binance (${d.list("positions").size})")
        if (d.list("positions").isEmpty()) Text("No open positions.", color = MX.Muted)
        d.list("positions").forEach { x ->
            Row(Modifier.fillMaxWidth().padding(vertical = 3.dp), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("${x.str("side")} ${x.str("symbol")} · ${x.num("leverage").toInt()}x", fontWeight = FontWeight.SemiBold)
                    Text("qty ${x.num("qty")} · entry ${x.num("entry").fmt(4)} · mark ${x.num("mark").fmt(4)} · " +
                            "liq ${x.num("liquidation").fmt(2)}", color = MX.Muted, style = MaterialTheme.typography.bodySmall)
                }
                Text(usd(x.num("unrealized")), color = pnlColor(x.num("unrealized")), fontFamily = Mono)
            }
        }
    }
    val bots = d.list("bot_results")
    MxCard {
        SectionTitle("Bot results on ${which.uppercase()} (closed trades)")
        if (bots.isEmpty()) Text("No bot trades closed on this account yet.", color = MX.Muted,
            style = MaterialTheme.typography.bodySmall)
        bots.forEach { r ->
            KV("${r.str("bot_id")} · ${r.num("w").toInt()}/${r.num("n").toInt()} wins", usd(r.num("pnl"), 4), pnlColor(r.num("pnl")))
        }
    }
    MxCard {
        SectionTitle("Account history (latest)")
        d.list("income").take(40).forEach { r ->
            Row(Modifier.fillMaxWidth()) {
                Text("${fmtTime(r.long("time"))} ${r.str("symbol")} ${r.str("type").lowercase().replace('_', ' ')}",
                    style = MaterialTheme.typography.bodySmall, modifier = Modifier.weight(1f))
                Text("${r.num("amount").fmt(4)} ${r.str("asset")}", color = pnlColor(r.num("amount")),
                    style = MaterialTheme.typography.bodySmall, fontFamily = Mono)
            }
        }
        if (d.list("income").isEmpty()) Text("No account activity in this period.", color = MX.Muted)
    }
}

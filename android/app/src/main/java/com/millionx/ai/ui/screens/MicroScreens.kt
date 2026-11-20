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
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
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
import com.millionx.ai.ui.components.KV
import com.millionx.ai.ui.components.LoadBox
import com.millionx.ai.ui.components.LocalAuth
import com.millionx.ai.ui.components.MultiLineChart
import com.millionx.ai.ui.components.MxCard
import com.millionx.ai.ui.components.RefreshIcon
import com.millionx.ai.ui.components.Screen
import com.millionx.ai.ui.components.SectionTitle
import com.millionx.ai.ui.components.StatusDot
import com.millionx.ai.ui.components.rememberResource
import com.millionx.ai.ui.theme.MX
import com.millionx.ai.ui.theme.pnlColor
import kotlinx.serialization.json.JsonElement
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

private val MICRO_SYMBOLS = listOf("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT", "ADAUSDT",
    "LINKUSDT", "AVAXUSDT", "LTCUSDT")

private fun qualityColor(q: String): Color = when (q) {
    "OK" -> MX.Up
    "DEGRADED" -> MX.Warn
    else -> MX.Down
}

/** 2.1 Research Data Command Center: live microstructure snapshot, recorded series, data quality,
 *  historical coverage and the A–F research results. Read-only except the collector switch. */
@Composable
fun MicroResearchScreen(nav: NavController) {
    var tab by rememberSaveable { mutableStateOf(0) }
    var sym by rememberSaveable { mutableStateOf("BTCUSDT") }
    var minutes by rememberSaveable { mutableStateOf(180) }
    val status = rememberResource("/api/micro/status", 10_000)
    val st by status.state.collectAsState()
    val auth = LocalAuth.current
    Screen("Research Data", nav, status.messages, actions = { RefreshIcon { status.refresh() } }) {
        item {
            Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf("Live", "Data quality", "Research").forEachIndexed { i, l -> FilterChip(tab == i, { tab = i }, { Text(l) }) }
            }
        }
        item {
            LoadBox(st, { status.refresh() }) { s ->
                MxCard {
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically) {
                        StatusDot(s.bool("running"), "Collector · WebSocket ${s["websocket"].str("state", "—")}")
                        if (s.bool("thread_alive")) OutlinedButton(onClick = {
                            auth("Stop recording") { status.action("POST", "/api/micro/collector", jsonBody("enabled" to false), "Recording stopped") }
                        }) { Text("Stop") }
                        else Button(onClick = {
                            auth("Start recording") { status.action("POST", "/api/micro/collector", jsonBody("enabled" to true), "Recording started") }
                        }) { Text("Record") }
                    }
                    Text("Records order book (top 20), trades, liquidations and funding for 10 instruments into a separate " +
                            "database. Research data only: nothing is traded. Uses mobile data and battery; a VPS is better for 24/7.",
                        color = MX.Muted, style = MaterialTheme.typography.bodySmall)
                }
            }
        }
        if (tab != 2) item {
            Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                MICRO_SYMBOLS.forEach { x -> FilterChip(sym == x, { sym = x }, { Text(x.removeSuffix("USDT")) }) }
            }
        }
        when (tab) {
            0 -> item { LiveTab(sym, minutes) { minutes = it } }
            1 -> item { QualityTab(st) }
            else -> item { MicroResearchTab() }
        }
    }
}

@Composable
private fun LiveTab(sym: String, minutes: Int, setMinutes: (Int) -> Unit) {
    val snap = rememberResource("/api/micro/snapshot/$sym", 5_000)
    val ser = rememberResource("/api/micro/series/$sym?minutes=$minutes", 30_000)
    val s1 by snap.state.collectAsState()
    val s2 by ser.state.collectAsState()
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        LoadBox(s1, { snap.refresh() }) { d ->
            MxCard {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                    SectionTitle("Snapshot $sym")
                    Chip(d.str("status"), if (d.str("status") == "COMPLETE") MX.Up else MX.Warn)
                }
                val f = d["fields"]
                f.keys().forEach { k ->
                    val x = f[k]
                    val q = x.str("quality")
                    val age = x.numOrNull("age_ms")?.let { " · ${"%.1f".format(it / 1000)} s" } ?: ""
                    KV("$k · ${x.str("source")}$age", q, qualityColor(q))
                    val v = x["value"]
                    if (v != null && v.keys().isNotEmpty()) {
                        Text(v.keys().take(8).joinToString("  ") { kk -> "$kk ${v.numOrNull(kk).fmt(4)}" },
                            color = MX.Muted, style = MaterialTheme.typography.bodySmall)
                    }
                    if (x.str("note").isNotEmpty()) Text(x.str("note"), color = MX.Muted, style = MaterialTheme.typography.bodySmall)
                }
                if (d.list("missing_critical").isNotEmpty())
                    Banner("INCOMPLETE: missing ${d.list("missing_critical").joinToString { it.toString().trim('"') }} — " +
                            "no analysis uses this snapshot as complete.", MX.Warn)
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf(60, 180, 720, 1440).forEach { m -> FilterChip(minutes == m, { setMinutes(m) }, { Text(if (m < 60) "${m}m" else "${m / 60}h") }) }
        }
        LoadBox(s2, { ser.refresh() }) { d ->
            val rows = d.list("rows")
            if (rows.isEmpty()) Banner(d.str("note").ifEmpty { "No recorded data for this window." }, MX.Info)
            else {
                fun series(key: String) = rows.mapNotNull { r -> r.numOrNull(key)?.let { r.long("t") to it } }
                MxCard { SectionTitle("Order book imbalance (top 10)"); MultiLineChart(listOf(MX.Info to series("imb10"))) }
                MxCard { SectionTitle("Spread (bps)"); MultiLineChart(listOf(MX.Warn to series("spread_bps"))) }
                MxCard {
                    SectionTitle("CVD (cumulative aggressive delta)")
                    var acc = 0.0
                    MultiLineChart(listOf(MX.Up to series("delta").map { (t, v) -> acc += v; t to acc }))
                }
                MxCard { SectionTitle("Funding rate"); MultiLineChart(listOf(MX.Accent to series("funding_rate"))) }
                MxCard { SectionTitle("Open interest"); MultiLineChart(listOf(MX.Info to series("oi"))) }
                MxCard {
                    SectionTitle("Liquidations (lower bound)")
                    val lq = d.list("liquidations")
                    KV("Events in window", lq.size.toString())
                    KV("Long liquidated (notional)", "%.0f".format(lq.filter { it.str("side") == "long_liquidated" }.sumOf { it.num("notional") }), MX.Down)
                    KV("Short liquidated (notional)", "%.0f".format(lq.filter { it.str("side") == "short_liquidated" }.sumOf { it.num("notional") }), MX.Up)
                    Text(d.str("liquidation_note"), color = MX.Muted, style = MaterialTheme.typography.bodySmall)
                }
            }
        }
    }
}

@Composable
private fun QualityTab(st: com.millionx.ai.ui.vm.Load<JsonElement>) {
    val res = rememberResource("/api/micro/research", 300_000)
    val r by res.state.collectAsState()
    val fmt = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.getDefault())
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        LoadBox(st, { }) { s ->
            MxCard {
                SectionTitle("Recorded data (this device / server)")
                val t = s["tables"]
                t.keys().forEach { k ->
                    val x = t[k]
                    val first = x.numOrNull("first_ts")?.let { fmt.format(Date(it.toLong())) } ?: "—"
                    KV("$k · ${x.num("rows").toLong()} rows", "since $first · age ${x.numOrNull("age_s").fmt(0)} s")
                }
            }
            MxCard {
                SectionTitle("Quality events (24 h)")
                val q = s["quality_24h"]
                if (q.keys().isEmpty()) Text("none", color = MX.Muted)
                q.keys().forEach { KV(it, q.num(it).toLong().toString(), MX.Warn) }
                s.list("quality_events").take(15).forEach { e ->
                    Text("${fmt.format(Date(e.long("ts")))} ${e.str("sym")} ${e.str("kind")}: ${e.str("detail")}",
                        color = MX.Muted, style = MaterialTheme.typography.bodySmall)
                }
            }
        }
        LoadBox(r, { res.refresh() }) { d ->
            val q = d["quality"]
            MxCard {
                SectionTitle("Historical coverage (archive, 2024-12 … 2026-06)")
                if (q == null) Text("not packaged in this build", color = MX.Muted)
                q.keys().sorted().forEach { sym ->
                    Text(sym, fontWeight = FontWeight.SemiBold)
                    val src = q[sym]
                    src.keys().forEach { k ->
                        val x = src[k]
                        KV("  $k", "${x.str("status")} · ${x.numOrNull("coverage").pct(1)}", qualityColor(x.str("status")))
                    }
                }
            }
            val a = d["availability"]
            if (a != null) MxCard {
                SectionTitle("What Binance publishes")
                a.keys().forEach { k -> KV(k, a.str(k)) }
            }
        }
    }
}

@Composable
private fun MicroResearchTab() {
    val res = rememberResource("/api/micro/research", 300_000)
    val r by res.state.collectAsState()
    LoadBox(r, { res.refresh() }) { d ->
        Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Banner(d.str("note"), MX.Info)
            val o = d["research"]
            if (o == null) {
                Banner("Research results are not packaged in this build yet.", MX.Warn)
                return@Column
            }
            val cands = o.list("edge_candidates")
            MxCard {
                SectionTitle("Result")
                KV("Tests (condition × horizon)", o.num("tests").toInt().toString())
                KV("Edge candidates", cands.size.toString(), if (cands.isEmpty()) MX.Muted else MX.Up)
                Text(if (cands.isEmpty()) "NO TRADE — no condition passed validation, FDR and stressed costs."
                     else cands.joinToString { it.toString().trim('"') }, color = MX.Muted, style = MaterialTheme.typography.bodySmall)
            }
            val st = o["status"]
            st.keys().forEach { k -> Banner("$k: ${st[k].str("status")} — ${st[k].str("reason")}", MX.Warn) }
            o.list("results").sortedByDescending { it["validation"].numOrNull("exp_bps") ?: -999.0 }.take(40).forEach { x ->
                val v = x["validation"]
                MxCard {
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                        Text("${x.str("condition")} · ${x.num("horizon_min").toInt()}m", fontWeight = FontWeight.SemiBold,
                            style = MaterialTheme.typography.bodyMedium)
                        if (x.bool("edge_candidate")) Chip("EDGE?", MX.Up)
                    }
                    KV("Direction (from discovery)", x.str("direction_from_discovery"))
                    KV("Net / trade (BASE)", "${v.numOrNull("exp_bps").fmt(1)} bps · n ${v.num("n").toInt()}", pnlColor(v.numOrNull("exp_bps")))
                    val ci = x.list("validation_ci95_bps")
                    KV("95 % CI · FDR q", "${ci.getOrNull(0)?.toString()?.toDoubleOrNull().fmt(1)} … " +
                            "${ci.getOrNull(1)?.toString()?.toDoubleOrNull().fmt(1)} bps · ${x.numOrNull("fdr_q").fmt(3)}")
                    KV("STRESSED / gross", "${x.numOrNull("stressed_exp_bps").fmt(1)} / ${x.numOrNull("validation_gross_bps").fmt(1)} bps")
                    KV("Win rate · PF · per day", "${v.numOrNull("win_rate").pct(0)} · ${v.numOrNull("pf").fmt(2)} · ${v.numOrNull("per_day").fmt(2)}")
                    KV("Instruments positive", "${x.num("symbols_positive").toInt()}/${x.num("symbols_tested").toInt()}")
                }
            }
        }
    }
}

@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package com.millionx.ai.ui.screens

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
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
import com.millionx.ai.data.arr
import com.millionx.ai.data.bool
import com.millionx.ai.data.fmt
import com.millionx.ai.data.get
import com.millionx.ai.data.list
import com.millionx.ai.data.long
import com.millionx.ai.data.num
import com.millionx.ai.data.numOrNull
import com.millionx.ai.data.str
import com.millionx.ai.ui.components.Banner
import com.millionx.ai.ui.components.BarList
import com.millionx.ai.ui.components.Candle
import com.millionx.ai.ui.components.CandleChart
import com.millionx.ai.ui.components.ChartLevel
import com.millionx.ai.ui.components.ChartMarker
import com.millionx.ai.ui.components.Chip
import com.millionx.ai.ui.components.KV
import com.millionx.ai.ui.components.LoadBox
import com.millionx.ai.ui.components.MultiLineChart
import com.millionx.ai.ui.components.MxCard
import com.millionx.ai.ui.components.RefreshIcon
import com.millionx.ai.ui.components.SERIES_COLORS
import com.millionx.ai.ui.components.Screen
import com.millionx.ai.ui.components.SectionTitle
import com.millionx.ai.ui.components.Stat
import com.millionx.ai.ui.components.rememberResource
import com.millionx.ai.ui.components.statusColor
import com.millionx.ai.ui.theme.MX
import com.millionx.ai.ui.theme.Mono
import com.millionx.ai.ui.theme.pnlColor
import com.millionx.ai.ui.vm.Load
import kotlinx.serialization.json.JsonElement

private val LIVE_EVENTS = setOf("signal", "decision", "trade", "position_opened", "approval_request")

/** LIVE: price chart with bot signals, signal funnel, shadow positions, competition and decision feed. */
@Composable
fun LiveScreen(nav: NavController) {
    var sym by rememberSaveable { mutableStateOf("BTCUSDT") }
    val chart = rememberResource("/api/candles/$sym?n=90", 10_000, setOf("signal"))
    val act = rememberResource("/api/activity?limit=40", 10_000, LIVE_EVENTS)
    val comp = rememberResource("/api/competition/curves?hours=72", 60_000, setOf("trade"))
    val c by chart.state.collectAsState()
    val a by act.state.collectAsState()
    val k by comp.state.collectAsState()
    // pick an available symbol once the engine reports its universe
    val avail = ((c as? Load.Ok<*>)?.data as? JsonElement).list("available").map { it.toString().trim('"') }
    LaunchedEffect(avail) { if (avail.isNotEmpty() && sym !in avail) sym = avail.first() }

    Screen("Live trading floor", nav, act.messages, actions = {
        RefreshIcon { chart.refresh(); act.refresh(); comp.refresh() }
    }) {
        item {
            MxCard {
                SectionTitle("$sym · 1 minute")
                Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    avail.forEach { s ->
                        FilterChip(selected = s == sym, onClick = { sym = s }, label = { Text(s.removeSuffix("USDT")) },
                            colors = FilterChipDefaults.filterChipColors(selectedContainerColor = MX.Surface2,
                                selectedLabelColor = MX.Accent))
                    }
                }
                LoadBox(c, { chart.refresh() }) { d ->
                    val candles = d.list("candles").map {
                        Candle(it.long("t"), it.num("o"), it.num("h"), it.num("l"), it.num("c"))
                    }
                    val markers = d.list("signals").map {
                        ChartMarker(it.long("ts"), it.num("price"), it.str("side") == "LONG", it.bool("accepted"))
                    }
                    val levels = d.list("levels").flatMap { p ->
                        val real = !p.bool("virtual")
                        listOf(ChartLevel(p.num("entry"), MX.Info.copy(alpha = if (real) 1f else 0.5f), !real),
                            ChartLevel(p.num("stop"), MX.Down.copy(alpha = if (real) 1f else 0.5f), true),
                            ChartLevel(p.num("tp2"), MX.Up.copy(alpha = if (real) 1f else 0.5f), true))
                    }
                    Column {
                        candles.lastOrNull()?.let { last ->
                            val first = candles.first()
                            val ch = (last.c - first.o) / first.o * 100
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text(last.c.fmt(if (last.c < 10) 4 else 2), style = MaterialTheme.typography.titleLarge,
                                    fontFamily = Mono, modifier = Modifier.weight(1f))
                                Text("${if (ch >= 0) "+" else ""}${ch.fmt(2)}% / 90 min", color = pnlColor(ch), fontFamily = Mono)
                            }
                        }
                        CandleChart(candles, markers, levels)
                        Text("▲ long / ▼ short signal (faded = rejected by supervisor) · dashed lines = open position " +
                                "entry / stop (red) / target (green); faint = shadow position",
                            color = MX.Muted, style = MaterialTheme.typography.bodySmall)
                        d.list("signals").takeLast(4).reversed().forEach { s ->
                            Text("${fmtTime(s.long("ts"))} ${s.str("bot").substringBefore('_')} ${s.str("side")} — " +
                                    s.str("reason").take(70), style = MaterialTheme.typography.bodySmall,
                                color = if (s.bool("accepted")) MX.Text else MX.Muted)
                        }
                    }
                }
            }
        }
        item {
            LoadBox(a, { act.refresh() }) { d ->
                Column(verticalArrangement = Arrangement.spacedBy(12.dp)) { FunnelCard(d) }
            }
        }
        item {
            LoadBox(a, { act.refresh() }) { d -> ShadowCard(d) }
        }
        item {
            MxCard {
                SectionTitle("Strategy competition — cumulative R (72 h, shadow trades)")
                LoadBox(k, { comp.refresh() }) { d ->
                    val bots = d.list("bots")
                    val series = bots.mapIndexed { i, b ->
                        SERIES_COLORS[i % SERIES_COLORS.size] to b.list("points").map { p ->
                            val arr = p.arr
                            arr[0].toString().toDouble().toLong() to arr[1].toString().toDouble()
                        }
                    }
                    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        MultiLineChart(series)
                        bots.forEachIndexed { i, b ->
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text("●", color = SERIES_COLORS[i % SERIES_COLORS.size])
                                Text(" ${b.str("bot")}", style = MaterialTheme.typography.bodySmall, modifier = Modifier.weight(1f))
                                Text("${b.num("trades").toInt()} tr · ${b.num("wins").toInt()} win · ",
                                    color = MX.Muted, style = MaterialTheme.typography.bodySmall)
                                Text("${b.num("sum_r").fmt(2)} R", color = pnlColor(b.num("sum_r")), fontFamily = Mono,
                                    style = MaterialTheme.typography.bodySmall)
                            }
                        }
                        Text("Every bot paper-trades each of its own signals with a fixed 0.5 R stake, so the curves are " +
                                "comparable. These are simulated results on real prices, not real money.",
                            color = MX.Muted, style = MaterialTheme.typography.bodySmall)
                    }
                }
            }
        }
        item { SectionTitle("Decision feed (every accepted signal and where it stopped)") }
        item {
            LoadBox(a, { act.refresh() }) { d ->
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    val list = d.list("decisions")
                    if (list.isEmpty()) Text("No decisions yet. Signals are being collected.", color = MX.Muted)
                    list.forEach { x -> DecisionRow(x) }
                }
            }
        }
    }
}

@Composable
private fun FunnelCard(d: JsonElement) {
    val f = d["funnel"]
    val eg = d["engine"]
    MxCard {
        SectionTitle("Engine status")
        val lvl = eg.str("level")
        val col = when (lvl) { "ok" -> MX.Up; "info" -> MX.Info; "warning" -> MX.Warn; else -> MX.Down }
        Text(eg.str("verdict"), color = col, style = MaterialTheme.typography.bodyMedium)
        if (eg.bool("running")) {
            KV("Markets watched", eg.list("universe").joinToString { it.toString().trim('"').removeSuffix("USDT") }
                .ifEmpty { "none" })
            KV("Candles analysed / bot signals", "${eg.num("bars_processed").toInt()} / ${eg.num("signals_since_start").toInt()}")
            KV("Running for", "${eg.num("uptime_min").toInt()} min")
            eg.numOrNull("last_bar_age_s")?.let { KV("Last candle", "${it.toInt()} s ago") }
        }
        eg.list("liquidity_failed").forEach { x ->
            Text("• ${x.str("symbol")}: spread ${x.num("spread_bps").fmt(2)} bps, depth ${x.num("depth").fmt(0)} USDT",
                color = MX.Warn, style = MaterialTheme.typography.bodySmall)
        }
        eg.list("errors").takeLast(3).forEach { e ->
            Text("! ${e.toString().trim('"').take(160)}", color = MX.Down, style = MaterialTheme.typography.bodySmall)
        }
    }
    MxCard {
        SectionTitle("Signal funnel — last 24 h")
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Stat("Signals", f.num("signals").toInt().toString(), Modifier.weight(1f))
            Stat("Selected", f.num("supervisor_accepted").toInt().toString(), Modifier.weight(1f), MX.Info)
            Stat("Traded", f.num("executed").toInt().toString(), Modifier.weight(1f), MX.Up)
            Stat("Blocked", f.num("blocked").toInt().toString(), Modifier.weight(1f), MX.Warn)
        }
        val waiting = f.num("awaiting_approval").toInt()
        if (waiting > 0 && d.str("execution") == "manual") {
            Banner("$waiting trade(s) waited for YOUR approval (execution = MANUAL). Approve them when the phone " +
                    "notifies you, or switch execution to AUTO in Owner Administration.", MX.Warn)
        }
        val top = d.list("top_block_reasons")
        if (f.num("executed").toInt() == 0 && top.isNotEmpty()) {
            Text("Why no trades were opened:", fontWeight = FontWeight.SemiBold)
        } else if (top.isNotEmpty()) {
            Text("Most common reasons a selected signal was blocked:", fontWeight = FontWeight.SemiBold)
        }
        top.forEach { r ->
            Text("• ${r.str("reason")}  ×${r.num("count").toInt()}", style = MaterialTheme.typography.bodySmall, color = MX.Warn)
        }
        val sup = d.list("top_supervisor_reasons")
        if (sup.isNotEmpty()) {
            Text("Supervisor filtered out:", color = MX.Muted, style = MaterialTheme.typography.bodySmall)
            BarList(sup.map { it.str("reason") to it.num("count") }, format = { it.toInt().toString() })
        }
    }
}

@Composable
private fun ShadowCard(d: JsonElement) {
    val sh = d.list("shadow_open")
    val s24 = d["shadow_24h"]
    MxCard {
        SectionTitle("Bots' paper positions now (${sh.size})")
        KV("Closed in 24 h", "${s24.num("trades").toInt()} trades · ${s24.num("wins").toInt()} wins · " +
                "${s24.num("sum_r").fmt(2)} R", pnlColor(s24.num("sum_r")))
        if (sh.isEmpty()) Text("No open paper positions right now.", color = MX.Muted, style = MaterialTheme.typography.bodySmall)
        sh.take(15).forEach { p ->
            val r = p.num("r_now")
            Row(Modifier.fillMaxWidth().padding(vertical = 2.dp), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("${p.str("side")} ${p.str("symbol")} · ${p.str("bot")}", fontWeight = FontWeight.SemiBold,
                        style = MaterialTheme.typography.bodySmall)
                    Text("entry ${p.num("entry").fmt(4)} → now ${p.num("price").fmt(4)} · SL ${p.num("stop").fmt(4)} · " +
                            "TP ${p.num("tp2").fmt(4)}", color = MX.Muted, style = MaterialTheme.typography.bodySmall)
                }
                Text("${if (r >= 0) "+" else ""}${r.fmt(2)} R", color = pnlColor(r), fontFamily = Mono)
            }
        }
    }
}

@Composable
private fun DecisionRow(x: JsonElement) {
    val out = x.str("outcome")
    val col: Color = when (out) {
        "executed" -> MX.Up
        "awaiting_owner_approval" -> MX.Warn
        else -> statusColor(if (out.startsWith("reject") || out == "vetoed") "REJECTED" else out)
    }
    MxCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("${x.str("side")} ${x.str("symbol")}", fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
            Chip(x.str("outcome_label", out), col)
        }
        Text("${fmtTime(x.long("ts"))} · ${x.str("bot")}", color = MX.Muted, style = MaterialTheme.typography.bodySmall)
        if (x.str("reason").isNotEmpty()) {
            Text("Stopped at ${x.str("stopped_at")}: ${x.str("reason")}", style = MaterialTheme.typography.bodySmall)
        }
    }
}

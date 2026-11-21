@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package com.millionx.ai.ui.screens

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.navigation.NavController
import com.millionx.ai.data.bool
import com.millionx.ai.data.fmt
import com.millionx.ai.data.get
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
import com.millionx.ai.ui.components.MxCard
import com.millionx.ai.ui.components.RefreshIcon
import com.millionx.ai.ui.components.Screen
import com.millionx.ai.ui.components.SectionTitle
import com.millionx.ai.ui.components.StatusDot
import com.millionx.ai.ui.components.rememberResource
import com.millionx.ai.ui.theme.MX
import com.millionx.ai.ui.theme.pnlColor
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** 2.2 Scalping dashboard: live scanner, LONG/SHORT opportunities ranked by expected net money, why each
 *  signal was accepted or rejected, virtual paper results, latency and costs, research verdict. */
@Composable
fun ScalpScreen(nav: NavController) {
    var tab by rememberSaveable { mutableStateOf(0) }
    val status = rememberResource("/api/scalp/status", 5_000)
    val st by status.state.collectAsState()
    Screen("Scalping", nav, status.messages, actions = { RefreshIcon { status.refresh() } }) {
        item {
            Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf("Scanner", "Opportunities", "Paper", "Research").forEachIndexed { i, l -> FilterChip(tab == i, { tab = i }, { Text(l) }) }
            }
        }
        item {
            LoadBox(st, { status.refresh() }) { s ->
                val stats = s["stats"]
                MxCard {
                    StatusDot(s.bool("running"), if (s.bool("running")) "Scanner running (every second)" else "Scanner stopped")
                    if (!s.bool("running")) Text("The scanner runs together with recording: More → Research Data → Record.",
                        color = MX.Muted, style = MaterialTheme.typography.bodySmall)
                    if (stats != null) {
                        KV("Seconds analysed", stats.num("seconds").toLong().toString())
                        KV("Detected / rejected / accepted / executed",
                            "${stats.num("detected").toLong()} / ${stats.num("rejected").toLong()} / ${stats.num("accepted").toLong()} / ${stats.num("executed").toLong()}")
                        KV("Engine C incomplete (no fresh book)", stats.num("incomplete").toLong().toString(), MX.Muted)
                        KV("Decision time (avg)", "${stats.numOrNull("decide_ms_avg").fmt(1)} ms")
                        val lat = stats["data_latency_ms"]
                        if (lat.keys().isNotEmpty()) KV("Exchange → app latency (median)",
                            lat.keys().joinToString { "${it.removeSuffix("USDT")} ${lat.num(it).toLong()}" } + " ms")
                    }
                    val l24 = s["last_24h"]
                    if (l24 != null) {
                        val bd = l24["by_decision"]
                        KV("24 h accepted / rejected", "${bd.numOrNull("ACCEPT").fmt(0)} / ${bd.numOrNull("REJECT").fmt(0)}")
                        KV("Missed (rejected but later profitable)", l24.num("missed_profitable").toLong().toString(), MX.Warn)
                    }
                    KV("Validated engines", s.list("validated").joinToString { it.toString().trim('"') }.ifEmpty { "none → NO TRADE" },
                        if (s.list("validated").isEmpty()) MX.Muted else MX.Up)
                }
                when (tab) {
                    0 -> ScannerList(s)
                    else -> {}
                }
            }
        }
        when (tab) {
            1 -> item { OppList() }
            2 -> item { PaperTab() }
            3 -> item { ScalpResearchTab() }
            else -> {}
        }
    }
}

@Composable
private fun ScannerList(s: kotlinx.serialization.json.JsonElement?) {
    val last = s["last"]
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        if (last.keys().isEmpty()) Banner("No live data yet.", MX.Info)
        last.keys().sorted().forEach { sym ->
            val sn = last[sym]["snapshot"]
            MxCard {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                    Text(sym.removeSuffix("USDT"), fontWeight = FontWeight.SemiBold)
                    Chip(sn.str("regime"), MX.Info)
                }
                val v = sn.numOrNull("v30z") ?: 0.0
                KV("Price · 30 s velocity", "${sn.numOrNull("price").fmt(4)} · ${"%+.1f".format(v)} σ",
                    if (v > 0) MX.Up else if (v < 0) MX.Down else MX.Muted)
                KV("Rel. volume · taker buy · CVD 10 s",
                    "${sn.numOrNull("relvol30").fmt(2)} · ${sn.numOrNull("taker30").pct(0)} · ${sn.numOrNull("cvd10").fmt(2)}")
                KV("3m momentum · 5m trend · 15m", "${sn.numOrNull("mom3m").fmt(1)} bps · ${sn.numOrNull("trend5").fmt(0)} · ${sn.numOrNull("ret15m").fmt(1)} bps")
                val b = sn["book"]
                KV("Book", if (b == null) "no fresh book (C incomplete)" else
                    "spread ${b.numOrNull("spread_bps").fmt(2)} bps · imbalance ${b.numOrNull("imb10").fmt(2)}", if (b == null) MX.Muted else MX.Text)
            }
        }
    }
}

@Composable
private fun OppList() {
    var dec by rememberSaveable { mutableStateOf("") }
    val r = rememberResource("/api/scalp/opportunities?limit=150" + (if (dec.isNotEmpty()) "&decision=$dec" else ""), 5_000)
    val st by r.state.collectAsState()
    val fmt = SimpleDateFormat("HH:mm:ss", Locale.getDefault())
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf("" to "All", "ACCEPT" to "Accepted", "REJECT" to "Rejected").forEach { (k, l) -> FilterChip(dec == k, { dec = k }, { Text(l) }) }
        }
        LoadBox(st, { r.refresh() }) { d ->
            val rows = d.list("rows").sortedByDescending { it["eval"].numOrNull("expected_net_eur") ?: -999.0 }
            if (rows.isEmpty()) Banner("No opportunities detected yet.", MX.Info)
            rows.take(80).forEach { o ->
                val ev = o["eval"]
                val acc = o.str("decision") == "ACCEPT"
                MxCard {
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                        Text("${fmt.format(Date(o.long("ts")))} ${o.str("sym").removeSuffix("USDT")} · engine ${o.str("engine")} · " +
                                (if (o.num("side") > 0) "LONG" else "SHORT"), fontWeight = FontWeight.SemiBold,
                            style = MaterialTheme.typography.bodyMedium)
                        Chip(o.str("decision"), if (acc) MX.Up else MX.Down)
                    }
                    KV("Expected net", "${ev.numOrNull("expected_net_r").fmt(2)} R = ${ev.numOrNull("expected_net_eur").fmt(2)} EUR",
                        pnlColor(ev.numOrNull("expected_net_eur")))
                    KV("Expected move · hold", "${ev.numOrNull("expected_move_bps").fmt(1)} bps · ${ev.numOrNull("expected_hold_s").fmt(0)} s")
                    KV("Gross − costs", "${ev.numOrNull("expected_gross_bps").fmt(1)} − ${ev.numOrNull("total_cost_bps").fmt(1)} bps " +
                            "(fees ${ev.numOrNull("entry_fee_bps").fmt(1)}+${ev.numOrNull("exit_fee_bps").fmt(1)}, spread ${ev.numOrNull("spread_bps").fmt(1)}, slip ${ev.numOrNull("slippage_bps").fmt(1)})")
                    KV("Stop · R/R · costs/risk", "${ev.numOrNull("stop_bps").fmt(1)} bps · ${ev.numOrNull("rr").fmt(1)} · ${ev.numOrNull("fee_to_risk").pct(0)}")
                    KV("Calibration", "${ev.str("calibration")} (n ${ev.num("calibration_n").toInt()}) · win rate ${ev.numOrNull("p_win").pct(0)}")
                    o.list("reasons").forEach { x ->
                        Text((if (acc) "✓ " else "✗ ") + x.toString().trim('"'), color = if (acc) MX.Up else MX.Down,
                            style = MaterialTheme.typography.bodySmall)
                    }
                    if (o["outcome_r"] != null && o.numOrNull("outcome_r") != null)
                        KV("Afterwards (shadow)", "${o.numOrNull("outcome_r").fmt(2)} R · ${o.str("outcome_reason")}", pnlColor(o.numOrNull("outcome_r")))
                }
            }
        }
    }
}

@Composable
private fun PaperTab() {
    val r = rememberResource("/api/scalp/paper", 10_000)
    val st by r.state.collectAsState()
    LoadBox(st, { r.refresh() }) { d ->
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Banner(d.str("note"), MX.Info)
            val s = d["summary"]
            MxCard {
                SectionTitle("Paper results (virtual)")
                if (s.keys().isEmpty()) Text("No paper trades: nothing has passed validation (NO TRADE).", color = MX.Muted)
                else {
                    KV("Trades", s.num("n").toInt().toString())
                    KV("Gross PnL", "%.2f EUR".format(s.num("gross_eur")), pnlColor(s.numOrNull("gross_eur")))
                    KV("Fees + spread + slippage", "%.2f EUR".format(s.num("fees_eur")), MX.Down)
                    KV("Net PnL", "%.2f EUR".format(s.num("net_eur")), pnlColor(s.numOrNull("net_eur")))
                    KV("Average R · win rate", "${s.numOrNull("avg_r").fmt(2)} R · ${s.numOrNull("win_rate").pct(0)}")
                    KV("Average holding time", "${s.numOrNull("avg_hold_s").fmt(0)} s")
                }
            }
            d.list("trades").take(50).forEach { t ->
                MxCard {
                    KV("${t.str("sym").removeSuffix("USDT")} · ${t.str("engine")} · ${if (t.num("side") > 0) "LONG" else "SHORT"} · ${t.num("lev").toInt()}x",
                        "%.2f EUR".format(t.num("pnl_eur")), pnlColor(t.numOrNull("pnl_eur")))
                    Text("${t.numOrNull("r").fmt(2)} R · ${t.str("reason")} · fees %.2f EUR".format(t.num("fees_eur")),
                        color = MX.Muted, style = MaterialTheme.typography.bodySmall)
                }
            }
        }
    }
}

@Composable
private fun ScalpResearchTab() {
    val r = rememberResource("/api/scalp/research", 300_000)
    val st by r.state.collectAsState()
    LoadBox(st, { r.refresh() }) { d ->
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            if (d["verdict"] == null) {
                Banner("Scalping research results are not packaged in this build.", MX.Warn)
                return@Column
            }
            val v = d["verdict"]
            MxCard {
                SectionTitle("Verdict (validation Oct 2025 – Jun 2026, event-driven 1 s)")
                v.keys().forEach { k -> KV(k, v.str(k), if (v.str(k) == "NO_TRADE") MX.Muted else MX.Up) }
                KV("Chosen on discovery", d["chosen"].keys().joinToString { "$it=${d["chosen"].str(it)}" })
            }
            val val_ = d["validation"]
            val_.keys().forEach { name ->
                val sc = val_[name]["scenarios"]
                MxCard {
                    SectionTitle("Engines $name · accepted ${val_[name].num("accepted").toInt()}")
                    sc.keys().forEach { k ->
                        val x = sc[k]
                        KV(k, if (x.num("n") == 0.0) "no trades" else
                            "n ${x.num("n").toInt()} · ${x.numOrNull("net_r").fmt(3)} R · ${x.numOrNull("net_eur_total").fmt(0)} EUR · PF ${x.numOrNull("pf").fmt(2)}",
                            pnlColor(x.numOrNull("net_r")))
                    }
                }
            }
            val p = d["portfolio"]
            p.keys().forEach { name ->
                MxCard {
                    SectionTitle("Portfolio $name (500 EUR, 2 positions)")
                    p[name].keys().forEach { lev ->
                        val x = p[name][lev]
                        KV(lev, "n ${x.num("n").toInt()} · ${x.numOrNull("net_eur_total").fmt(1)} EUR · ${x.numOrNull("trades_per_day").fmt(2)}/day",
                            pnlColor(x.numOrNull("net_eur_total")))
                    }
                }
            }
        }
    }
}

@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package com.millionx.ai.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
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
import com.millionx.ai.data.arr
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
import com.millionx.ai.ui.components.rememberResource
import com.millionx.ai.ui.theme.MX
import com.millionx.ai.ui.theme.pnlColor
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.doubleOrNull
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

private fun JsonElement?.at(i: Int): Double? = (this.arr.getOrNull(i) as? JsonPrimitive)?.doubleOrNull

private fun verdictColor(v: String) = when (v) {
    "CANDIDATE_FOR_PAPER" -> MX.Up
    "NO_TRADE" -> MX.Muted
    else -> MX.Warn
}

/** Research 2.0: CI results on real data, Strategy Selection Engine, research-only strategies,
 *  decay warnings and the versioned parameter history. Read-only; nothing here activates a bot. */
@Composable
fun Research2Screen(nav: NavController) {
    val vm = rememberResource("/api/research2", 120_000, setOf("trade"))
    val st by vm.state.collectAsState()
    var tab by rememberSaveable { mutableStateOf(0) }
    Screen("Research 2.0", nav, vm.messages, actions = { RefreshIcon { vm.refresh() } }) {
        item {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf("Selection", "Strategies", "Research only", "History").forEachIndexed { i, l ->
                    FilterChip(tab == i, { tab = i }, { Text(l) })
                }
            }
        }
        item {
            LoadBox(st, { vm.refresh() }) { d ->
                Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Banner(d.str("note"), MX.Info)
                    if (!d.bool("available")) {
                        Banner("No Research 2.0 results imported yet. The research runs on CI (real Binance data); " +
                                "its results ship with the next build.", MX.Warn)
                    }
                    val rep = d["report"]
                    when (tab) {
                        0 -> SelectionTab(rep, d)
                        1 -> StrategiesTab(rep)
                        2 -> ResearchOnlyTab(d)
                        else -> HistoryTab(d)
                    }
                }
            }
        }
    }
}

@Composable
private fun SelectionTab(rep: JsonElement?, d: JsonElement?) {
    val cells = rep.list("cells")
    MxCard {
        SectionTitle("Best strategy per instrument & regime")
        val chosen = rep.list("chosen").map { (it as? JsonPrimitive)?.content ?: "" }
        KV("Cells", cells.size.toString())
        KV("NO TRADE cells", rep.num("no_trade_cells").toInt().toString(),
            if (rep.num("no_trade_cells").toInt() == cells.size) MX.Muted else MX.Text)
        KV("Selected strategies", chosen.joinToString().ifEmpty { "none" })
        Text("A cell trades only if a validated strategy has ≥ 30 OOS trades there, a positive lower 95 % " +
                "bound after decay, and a positive score after shrinkage and penalties. Otherwise NO TRADE.",
            color = MX.Muted, style = MaterialTheme.typography.bodySmall)
    }
    cells.groupBy { it.str("symbol") }.toSortedMap().forEach { (sym, cs) ->
        MxCard {
            SectionTitle(sym)
            cs.forEach { c ->
                val sel = c.str("selected")
                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.SpaceBetween) {
                    Text(c.str("regime"), style = MaterialTheme.typography.bodyMedium)
                    Chip(if (sel == "NO_TRADE") "NO TRADE" else sel, verdictColor(if (sel == "NO_TRADE") sel else "CANDIDATE_FOR_PAPER"))
                }
                val best = c.str("best_candidate")
                if (best.isNotEmpty()) {
                    Text("best candidate $best · n ${c.num("best_n").toInt()} · CI low ${c.numOrNull("best_ci_low").fmt(3)} R" +
                            " · score ${c.numOrNull("best_score").fmt(3)}", color = MX.Muted,
                        style = MaterialTheme.typography.bodySmall)
                }
            }
        }
    }
    val decay = d.list("decay_warnings")
    MxCard {
        SectionTitle("Decay warnings (recent vs validated)")
        if (decay.isEmpty()) Text("none", color = MX.Muted)
        decay.forEach { w ->
            KV(w.str("bot"), "${w.numOrNull("recent_mean_r").fmt(3)} R (n ${w.num("n").toInt()})", MX.Down)
            Text(w.str("basis"), color = MX.Muted, style = MaterialTheme.typography.bodySmall)
        }
    }
}

@Composable
private fun StrategiesTab(rep: JsonElement?) {
    val reps = rep.list("reports").sortedByDescending { it["oos"].numOrNull("expectancy_r") ?: -9.0 }
    if (reps.isEmpty()) Text("no results", color = MX.Muted)
    reps.forEach { r ->
        val o = r["oos"]
        val v = r.str("verdict")
        MxCard {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically) {
                Text("${r.str("bot")} · ${r.num("timeframe").toInt()}m", fontWeight = FontWeight.SemiBold)
                Chip(if (v == "CANDIDATE_FOR_PAPER") "PAPER CANDIDATE" else v, verdictColor(v))
            }
            KV("OOS trades", o.num("trades").toInt().toString())
            KV("Expectancy (BASE costs)", "${o.numOrNull("expectancy_r").fmt(3)} R", pnlColor(o.numOrNull("expectancy_r")))
            val ci = r["bootstrap"]["exp_r_ci95"]
            KV("95 % CI (block bootstrap)", "${ci.at(0).fmt(3)} … ${ci.at(1).fmt(3)} R", pnlColor(ci.at(0)))
            KV("Profit factor / win rate", "${o.numOrNull("profit_factor").fmt(2)} / ${o.numOrNull("win_rate").pct(0)}")
            KV("Positive WF windows", r.numOrNull("positive_windows").pct(0))
            KV("Max drawdown (0.5 % risk)", r.numOrNull("max_drawdown").pct(1))
            KV("Deflated Sharpe / PBO", "${r["deflated_sharpe"].numOrNull("dsr").fmt(2)} / ${r["pbo"].numOrNull("pbo").fmt(2)}")
            val c = r["cost_scenarios"]
            KV("Exp R STRESSED / EXTREME", "${c.numOrNull("STRESSED").fmt(3)} / ${c.numOrNull("EXTREME").fmt(3)}",
                pnlColor(c.numOrNull("STRESSED")))
            KV("Configurations tried", r.num("configs_tried").toInt().toString())
            val failed = r["checks"].keys().filter { !r["checks"][it].bool("pass") }
            Text(if (failed.isEmpty()) "all activation checks passed" else "failed: " + failed.joinToString(),
                color = if (failed.isEmpty()) MX.Up else MX.Muted, style = MaterialTheme.typography.bodySmall)
        }
    }
}

@Composable
private fun ResearchOnlyTab(d: JsonElement?) {
    val statuses = d["statuses"]
    MxCard {
        SectionTitle("Research-only strategies (never traded live or on paper)")
        d.list("catalog").filter { it.bool("research_only") }.forEach { c ->
            KV(c.str("name"), "v${c.str("version")}", MX.Muted)
        }
    }
    MxCard {
        SectionTitle("Suspended / not tradable bots")
        val off = statuses.keys().filter { statuses.str(it) !in setOf("PAPER", "BACKTEST_PASSED", "LIVE_ELIGIBLE") }
        if (off.isEmpty()) Text("none", color = MX.Muted)
        off.sorted().forEach { KV(it, statuses.str(it), MX.Warn) }
    }
}

@Composable
private fun HistoryTab(d: JsonElement?) {
    val fmt = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.getDefault())
    MxCard {
        SectionTitle("Parameter & validation history (hash-chained audit)")
        val h = d.list("param_history")
        if (h.isEmpty()) Text("no changes recorded", color = MX.Muted)
        h.forEach { e ->
            Text("${fmt.format(Date(e.long("ts")))} · ${e.str("action")} · ${e.str("actor")}",
                fontWeight = FontWeight.SemiBold, style = MaterialTheme.typography.bodySmall)
            Text(e["detail"].toString().take(300), color = MX.Muted, style = MaterialTheme.typography.bodySmall)
        }
    }
}

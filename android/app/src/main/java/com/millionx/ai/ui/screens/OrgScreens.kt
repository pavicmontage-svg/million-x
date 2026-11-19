@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package com.millionx.ai.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
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
import com.millionx.ai.data.jsonBody
import com.millionx.ai.data.keys
import com.millionx.ai.data.list
import com.millionx.ai.data.num
import com.millionx.ai.data.numOrNull
import com.millionx.ai.data.str
import com.millionx.ai.ui.components.Banner
import com.millionx.ai.ui.components.Chip
import com.millionx.ai.ui.components.KV
import com.millionx.ai.ui.components.LoadBox
import com.millionx.ai.ui.components.LocalAuth
import com.millionx.ai.ui.components.MxCard
import com.millionx.ai.ui.components.RefreshIcon
import com.millionx.ai.ui.components.Screen
import com.millionx.ai.ui.components.SectionTitle
import com.millionx.ai.ui.components.StatusDot
import com.millionx.ai.ui.components.rememberResource
import com.millionx.ai.ui.components.statusColor
import com.millionx.ai.ui.theme.MX
import kotlinx.serialization.json.JsonElement

/** AI BOARDROOM: five executive agents, latest daily meeting, disagreements. */
@Composable
fun BoardroomScreen(nav: NavController) {
    val agents = rememberResource("/api/org/agents", 30_000)
    val meetings = rememberResource("/api/org/meetings?limit=5", 60_000, setOf("alert"))
    val a by agents.state.collectAsState()
    val m by meetings.state.collectAsState()
    val auth = LocalAuth.current
    Screen("AI Boardroom", nav, meetings.messages, actions = { RefreshIcon { agents.refresh(); meetings.refresh() } }) {
        item {
            Banner("Executives are AI agents. They advise and can only tighten risk automatically; " +
                    "everything else waits for your decision. No activity is simulated.", MX.Info)
        }
        item { SectionTitle("Executive team") }
        item {
            LoadBox(a, { agents.refresh() }) { list ->
                Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    list.arr.filter { it.str("kind") == "executive" }.forEach { e ->
                        MxCard(onClick = { nav.navigate("agent/${e.str("id")}") }) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Column(Modifier.weight(1f)) {
                                    Text("${e.str("name")} — ${e.str("title")}", fontWeight = FontWeight.SemiBold)
                                    Text(e.str("responsibility"), color = MX.Muted, style = MaterialTheme.typography.bodySmall)
                                }
                                StatusDot(e.str("status") == "online", e.str("status"))
                            }
                            Text("Latest: ${e.str("last_summary", "no decision yet")}", style = MaterialTheme.typography.bodySmall)
                            if (e.str("last_error").isNotEmpty()) Text("Alert: ${e.str("last_error")}", color = MX.Warn,
                                style = MaterialTheme.typography.bodySmall)
                        }
                    }
                }
            }
        }
        item {
            SectionTitle("Board meetings") {
                androidx.compose.material3.TextButton(onClick = {
                    auth("Convene board meeting") { meetings.action("POST", "/api/org/meetings", "{}", "Meeting finished") }
                }) { Text("Convene now") }
            }
        }
        item {
            LoadBox(m, { meetings.refresh() }) { list ->
                Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    if (list.arr.isEmpty()) Text("No meetings yet. One runs automatically each UTC day.", color = MX.Muted)
                    list.arr.forEach { MeetingCard(it) }
                }
            }
        }
    }
}

@Composable
private fun MeetingCard(mt: JsonElement) {
    MxCard {
        Text("Meeting ${mt.str("id")} · ${fmtTime(mt.num("ts").toLong())}", fontWeight = FontWeight.SemiBold)
        val dis = mt.list("disagreements")
        if (dis.isNotEmpty()) {
            SectionTitle("Disagreements")
            dis.forEach { d ->
                val pos = d["positions"]
                Text("${d.str("topic")}: " + pos.keys().joinToString { "${it.removePrefix("exec_").uppercase()}=${pos.str(it)}" },
                    color = MX.Warn, style = MaterialTheme.typography.bodySmall)
            }
        } else Text("No recorded disagreements", color = MX.Muted, style = MaterialTheme.typography.bodySmall)
        val outs = mt["outputs"]
        outs.keys().forEach { k ->
            val o = outs[k]
            Text("${k.removePrefix("exec_").uppercase()} (${o.str("mode")}): ${o.str("summary")}", style = MaterialTheme.typography.bodySmall)
        }
        val recs = mt.list("recommendations")
        if (recs.isNotEmpty()) SectionTitle("Recommendations")
        recs.forEach { r ->
            Text("• [${r.str("from").removePrefix("exec_").uppercase()}] ${r.str("action")} ${r.str("target")} " +
                    "(${r.str("direction")}) — ${r.str("status")}", style = MaterialTheme.typography.bodySmall,
                color = if (r.bool("traceable", true)) MX.Text else MX.Warn)
            Text("   evidence: ${r.list("evidence_ids").joinToString { it.toString().trim('"') }}", color = MX.Muted,
                style = MaterialTheme.typography.bodySmall)
        }
    }
}

/** SIX-BRAIN CONTROL CENTER. */
@Composable
fun BrainsScreen(nav: NavController) {
    val agents = rememberResource("/api/org/agents", 30_000)
    val a by agents.state.collectAsState()
    Screen("Six-Brain Control Center", nav, agents.messages, actions = { RefreshIcon { agents.refresh() } }) {
        item {
            LoadBox(a, { agents.refresh() }) { list ->
                Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    list.arr.filter { it.str("kind") == "brain" }.forEach { b ->
                        val tot = b["totals"]
                        MxCard(onClick = { nav.navigate("agent/${b.str("id")}") }) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text(b.str("title"), fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                                Chip(b.str("status").uppercase(), statusColor(b.str("status")))
                            }
                            KV("Model / prompt", "${b.str("model")} · v${b.num("prompt_version").toInt()}")
                            KV("Confidence", b.numOrNull("confidence").fmt(2))
                            KV("Runs / errors", "${tot.num("runs").toInt()} / ${tot.num("errors").toInt()}")
                            KV("Tokens in/out", "${tot.num("tin").toLong()} / ${tot.num("tout").toLong()}")
                            KV("API cost", tot.numOrNull("cost")?.let { "$" + it.fmt(4) } ?: "price not configured")
                            Text(b.str("last_summary", "no run yet"), color = MX.Muted, style = MaterialTheme.typography.bodySmall)
                        }
                    }
                }
            }
        }
    }
}

/** Agent detail: prompt editor (owner + biometric), versions, config, logs. */
@Composable
fun AgentDetailScreen(nav: NavController, id: String) {
    val vm = rememberResource("/api/org/agents/$id", 30_000)
    val st by vm.state.collectAsState()
    val auth = LocalAuth.current
    var draft by remember { mutableStateOf<String?>(null) }
    var note by remember { mutableStateOf("") }
    Screen(id, nav, vm.messages, actions = { RefreshIcon { vm.refresh() } }) {
        item {
            LoadBox(st, { vm.refresh() }) { d ->
                val ag = d["agent"]; val cfg = d["config"]; val pr = d["prompt"]
                Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    MxCard {
                        Text("${ag.str("name")} — ${ag.str("title")}", style = MaterialTheme.typography.headlineSmall)
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text("Enabled", modifier = Modifier.weight(1f))
                            Switch(checked = cfg.bool("enabled"), onCheckedChange = { on ->
                                auth("Change agent") { vm.action("PUT", "/api/org/agents/$id/config", jsonBody("enabled" to on), "Saved") }
                            })
                        }
                        KV("Model", cfg.str("model"))
                        KV("Max tokens / temperature", "${cfg.num("max_tokens").toInt()} / ${cfg.num("temperature").fmt(2)}")
                        KV("Auto-apply rights", ag.list("auto_apply").joinToString { it.toString().trim('"') }.ifEmpty { "none (advisory only)" })
                        OutlinedButton(onClick = { auth("Run agent") { vm.action("POST", "/api/org/agents/$id/run", "{}", "Run finished") } }) {
                            Text("Run now")
                        }
                    }
                    MxCard {
                        SectionTitle("System prompt v${pr.num("version").toInt()} (owner only, versioned)")
                        OutlinedTextField(value = draft ?: pr.str("prompt"), onValueChange = { draft = it },
                            modifier = Modifier.fillMaxWidth().heightIn(min = 200.dp))
                        OutlinedTextField(value = note, onValueChange = { note = it }, label = { Text("Change note") },
                            singleLine = true, modifier = Modifier.fillMaxWidth())
                        Button(enabled = draft != null, onClick = {
                            auth("Publish new prompt version") {
                                vm.action("PUT", "/api/org/agents/$id/prompt", jsonBody("prompt" to draft, "note" to note),
                                    "New version published") { draft = null; note = "" }
                            }
                        }) { Text("Publish new version") }
                        Text("The output contract (evidence ids, no order rights) is appended by the server and cannot be edited.",
                            color = MX.Muted, style = MaterialTheme.typography.bodySmall)
                    }
                    MxCard {
                        SectionTitle("Version history")
                        d.list("versions").forEach { v ->
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text("v${v.num("version").toInt()} · ${v.str("author")} · ${fmtTime(v.num("created_at").toLong())}",
                                    modifier = Modifier.weight(1f), style = MaterialTheme.typography.bodySmall)
                                androidx.compose.material3.TextButton(onClick = {
                                    auth("Activate version") {
                                        vm.action("POST", "/api/org/agents/$id/activate", jsonBody("version" to v.num("version").toInt()), "Activated")
                                    }
                                }) { Text("Activate") }
                            }
                        }
                    }
                    SectionTitle("Decision history (input/output logs)")
                    d.list("runs").forEach { r ->
                        val o = r["output"]
                        MxCard {
                            Row {
                                Text(fmtTime(r.num("ts").toLong()), modifier = Modifier.weight(1f), fontWeight = FontWeight.SemiBold)
                                Chip(o.str("mode", "?"), if (o.str("mode") == "llm") MX.Up else MX.Info)
                            }
                            Text(o.str("summary"), style = MaterialTheme.typography.bodySmall)
                            o.list("findings").take(5).forEach { f ->
                                Text("• ${f.str("text")}", style = MaterialTheme.typography.bodySmall,
                                    color = if (f.bool("traceable", true)) MX.Text else MX.Warn)
                            }
                            o.list("recommendations").forEach { rc ->
                                Text("→ ${rc.str("action")} ${rc.str("target")} (${rc.str("direction")})", color = MX.Info,
                                    style = MaterialTheme.typography.bodySmall)
                            }
                            KV("Tokens", "${r.num("input_tokens").toInt()} in / ${r.num("output_tokens").toInt()} out · ${r.num("latency_ms").toInt()} ms")
                            if (r.str("error").isNotEmpty()) Text("Error: ${r.str("error")}", color = MX.Down, style = MaterialTheme.typography.bodySmall)
                            if (o.str("note").isNotEmpty()) Text(o.str("note"), color = MX.Muted, style = MaterialTheme.typography.bodySmall)
                        }
                    }
                }
            }
        }
    }
}

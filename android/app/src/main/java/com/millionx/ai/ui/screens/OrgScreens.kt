@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package com.millionx.ai.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
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
import com.millionx.ai.ui.components.agentStatusLabel
import com.millionx.ai.data.long
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
            Banner("Executives are AI agents. Agenda: CEO, CIO, Quant, CRO, COO reports → discussion → CEO decision → " +
                    "independent CRO approval/veto. Only CRO-approved tightening is applied automatically; everything " +
                    "else waits for you. Items marked RULES ran with fixed rules, without a language model.", MX.Info)
        }
        item {
            MxCard(onClick = { nav.navigate("ai_center") }) {
                SectionTitle("AI Command Center") { Text("Open ›", color = MX.Accent) }
                Text("Independent Trader, AI Supervisor, free providers Groq → Gemini → OpenRouter with failover.",
                    color = MX.Muted, style = MaterialTheme.typography.bodySmall)
            }
        }
        item { AiModelCard() }
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
                                StatusDot(e.bool("active"), agentStatusLabel(e.str("status")), statusColor(e.str("status")))
                            }
                            Text("Latest: ${e.str("last_summary", "no decision yet")}" +
                                    (e.long("last_run").takeIf { it > 0 }?.let { " · ${fmtTime(it)}" } ?: ""),
                                style = MaterialTheme.typography.bodySmall)
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
        item { SectionTitle("Work log — every agent run, newest first") }
        item { WorkLog() }
    }
}

/** Real agent runs (rules or AI model), newest first. Nothing here is simulated. */
@Composable
fun WorkLog() {
    val vm = rememberResource("/api/org/activity?limit=40", 20_000, setOf("alert"))
    val st by vm.state.collectAsState()
    LoadBox(st, { vm.refresh() }) { d ->
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            val next = d.num("next_cycle_in_ms").toLong()
            Text("Brains run every ${d.num("interval_min").toInt()} min · next run in ${next / 60_000} min ${(next / 1000) % 60} s. " +
                    "Executives meet once a day (or tap Convene now).", color = MX.Muted, style = MaterialTheme.typography.bodySmall)
            val runs = d.list("runs")
            if (runs.isEmpty()) Text("No agent has run yet.", color = MX.Muted)
            runs.forEach { r ->
                MxCard {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text("${r.str("agent")} · ${r.str("title")}", fontWeight = FontWeight.SemiBold,
                            modifier = Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium)
                        ModeChip(r.str("mode"))
                    }
                    Text("${fmtTime(r.long("ts"))} · ${r.str("kind")}" +
                            (if (r.str("mode") == "llm") " · ${r.str("model")} · ${r.num("tokens").toInt()} tokens · " +
                                    "${r.num("latency_ms").toInt()} ms" else ""),
                        color = MX.Muted, style = MaterialTheme.typography.bodySmall)
                    Text(r.str("summary"), style = MaterialTheme.typography.bodySmall)
                    r.list("findings").forEach { f ->
                        Text("• ${f.toString().trim('"')}", style = MaterialTheme.typography.bodySmall)
                    }
                    r.list("recommendations").forEach { f ->
                        Text("→ ${f.toString().trim('"')}", color = MX.Info, style = MaterialTheme.typography.bodySmall)
                    }
                    if (r.str("error").isNotEmpty()) Text("AI error: ${r.str("error").take(160)}", color = MX.Warn,
                        style = MaterialTheme.typography.bodySmall)
                }
            }
        }
    }
}

private data class Provider(val id: String, val label: String, val help: String)

private val PROVIDERS = listOf(
    Provider("groq", "Groq (free tier)", "Free key: console.groq.com → API Keys. Rate-limited; calls are spaced automatically."),
    Provider("gemini", "Google Gemini (free tier)", "Free key: aistudio.google.com/apikey. Enter a current Flash model name."),
    Provider("openai_compatible", "Other OpenAI-compatible", "Any /chat/completions endpoint: base URL + model + key."),
    Provider("anthropic", "Anthropic Claude (paid)", "Key from console.anthropic.com; usage is billed."),
)

/** Connect the agents to a language model (key stored encrypted on the engine). */
@Composable
fun AiModelCard() {
    val vm = rememberResource("/api/settings/llm", 60_000)
    val st by vm.state.collectAsState()
    val auth = LocalAuth.current
    var open by remember { mutableStateOf(false) }
    var msg by remember { mutableStateOf<String?>(null) }
    androidx.compose.runtime.LaunchedEffect(vm) { vm.messages.collect { msg = it } }
    LoadBox(st, { vm.refresh() }) { d ->
        MxCard {
            msg?.let { Text(it, color = if (it.contains("fail", true) || it.contains("error", true)) MX.Down else MX.Up,
                style = MaterialTheme.typography.bodySmall) }
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("AI model for the team", fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                StatusDot(d.bool("connected"), if (d.bool("connected")) "connected" else "not connected",
                    if (d.bool("connected")) MX.Up else MX.Warn)
            }
            if (d.bool("connected")) {
                KV("Provider", d.str("provider"))
                KV("Model", d.str("model"))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedButton(onClick = { open = true }) { Text("Change") }
                    OutlinedButton(onClick = { auth("Disconnect AI model") {
                        vm.action("PUT", "/api/settings/llm", jsonBody("disconnect" to true), "Disconnected") } }) {
                        Text("Disconnect", color = MX.Down)
                    }
                }
            } else {
                Text("Without a model the agents still work, but only with fixed rules (shown as RULES). " +
                        "Connect a free model to get real AI analysis.", color = MX.Muted, style = MaterialTheme.typography.bodySmall)
                Button(onClick = { open = true }) { Text("Connect AI model (free option)") }
            }
        }
        if (open) {
            val presets = d["presets"]
            var prov by remember { mutableStateOf("groq") }
            var key by remember { mutableStateOf("") }
            var model by remember { mutableStateOf(presets[prov].str("model")) }
            var url by remember { mutableStateOf(presets[prov].str("base_url")) }
            androidx.compose.material3.AlertDialog(
                onDismissRequest = { open = false },
                title = { Text("Connect AI model") },
                text = {
                    Column(Modifier.heightIn(max = 520.dp).verticalScroll(rememberScrollState()),
                        verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        PROVIDERS.forEach { p ->
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                androidx.compose.material3.RadioButton(selected = prov == p.id, onClick = {
                                    prov = p.id; model = presets[p.id].str("model"); url = presets[p.id].str("base_url")
                                })
                                Text(p.label)
                            }
                        }
                        Text(PROVIDERS.first { it.id == prov }.help, color = MX.Muted, style = MaterialTheme.typography.bodySmall)
                        OutlinedTextField(key, { key = it.trim() }, label = { Text("API key") }, singleLine = true,
                            visualTransformation = androidx.compose.ui.text.input.PasswordVisualTransformation(),
                            modifier = Modifier.fillMaxWidth())
                        OutlinedTextField(model, { model = it.trim() }, label = { Text("Model") }, singleLine = true,
                            modifier = Modifier.fillMaxWidth())
                        if (prov != "groq" && prov != "anthropic") {
                            OutlinedTextField(url, { url = it.trim() }, label = { Text("Base URL") }, singleLine = true,
                                modifier = Modifier.fillMaxWidth())
                        }
                        Text("The key is tested with one short request, then stored encrypted on the engine. " +
                                "AI agents are advisory and can only tighten risk.", color = MX.Muted,
                            style = MaterialTheme.typography.bodySmall)
                    }
                },
                confirmButton = {
                    Button(enabled = key.length >= 10 && model.isNotEmpty(), onClick = {
                        auth("Connect AI model") {
                            vm.action("PUT", "/api/settings/llm",
                                jsonBody("provider" to prov, "api_key" to key, "model" to model, "base_url" to url),
                                "AI model connected — agents use it from the next run")
                            open = false
                        }
                    }) { Text("Connect & test") }
                },
                dismissButton = { androidx.compose.material3.TextButton(onClick = { open = false }) { Text("Cancel") } },
            )
        }
    }
}

@Composable
private fun ModeChip(mode: String) {
    when (mode) {
        "llm" -> Chip("AI (LLM)", MX.Up)
        "deterministic" -> Chip("RULES", MX.Info)
        "" -> {}
        else -> Chip(mode.uppercase(), MX.Warn)
    }
}

@Composable
private fun MeetingCard(mt: JsonElement) {
    MxCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Meeting ${mt.str("id")} · ${fmtTime(mt.num("ts").toLong())}", fontWeight = FontWeight.SemiBold,
                modifier = Modifier.weight(1f))
            if (!mt.bool("llm", true)) Chip("LLM OFFLINE", MX.Warn)
        }
        val agenda = mt.list("agenda")
        if (agenda.isEmpty()) {
            Text("Meeting from an older version (no agenda recorded)", color = MX.Muted, style = MaterialTheme.typography.bodySmall)
        }
        agenda.forEach { it0 ->
            val n = it0.num("item").toInt()
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("$n. ${it0.str("title")}", fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                ModeChip(it0.str("mode"))
            }
            if (it0.str("summary").isNotEmpty()) Text(it0.str("summary"), style = MaterialTheme.typography.bodySmall)
            if (it0.str("note").isNotEmpty()) Text(it0.str("note"), color = MX.Muted, style = MaterialTheme.typography.bodySmall)
            if (n == 6) {
                val dis = it0.list("disagreements")
                if (dis.isEmpty()) Text("No disagreements recorded", color = MX.Muted, style = MaterialTheme.typography.bodySmall)
                dis.forEach { d ->
                    val pos = d["positions"]
                    Text("${d.str("topic")}: " + pos.keys().joinToString { "${it.removePrefix("exec_").uppercase()}=${pos.str(it)}" },
                        color = MX.Warn, style = MaterialTheme.typography.bodySmall)
                }
                it0.list("discussion").forEach { x ->
                    Text("↳ ${x.str("agent").removePrefix("exec_").uppercase()} → ${x.str("final_stance")}: ${x.str("summary")}",
                        style = MaterialTheme.typography.bodySmall)
                }
            }
            if (n == 8) {
                it0.list("verdicts").forEach { v ->
                    val vetoed = v.str("cro").startsWith("vetoed")
                    Text("• ${v.str("action")} ${v.str("target")} (${v.str("direction")}) → ${v.str("status")}" +
                            (if (vetoed) " — ${v.str("cro_reason")}" else ""),
                        color = if (vetoed) MX.Down else MX.Up, style = MaterialTheme.typography.bodySmall)
                    Text("   evidence: ${v.list("evidence_ids").joinToString { it.toString().trim('"') }}", color = MX.Muted,
                        style = MaterialTheme.typography.bodySmall)
                }
                if (it0.list("verdicts").isEmpty()) Text("No decisions to review", color = MX.Muted,
                    style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}

/** SIX-BRAIN CONTROL CENTER. */
@Composable
fun BrainsScreen(nav: NavController) {
    val agents = rememberResource("/api/org/agents", 30_000)
    val a by agents.state.collectAsState()
    Screen("Six-Brain Control Center", nav, agents.messages, actions = { RefreshIcon { agents.refresh() } }) {
        item { AiModelCard() }
        item {
            LoadBox(a, { agents.refresh() }) { list ->
                Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    list.arr.filter { it.str("kind") == "brain" }.forEach { b ->
                        val tot = b["totals"]
                        MxCard(onClick = { nav.navigate("agent/${b.str("id")}") }) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text(b.str("title"), fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                                Chip(agentStatusLabel(b.str("status")).uppercase(), statusColor(b.str("status")))
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
                                ModeChip(o.str("mode", "?"))
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

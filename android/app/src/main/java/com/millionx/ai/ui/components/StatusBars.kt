package com.millionx.ai.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.PowerSettingsNew
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.millionx.ai.data.bool
import com.millionx.ai.data.get
import com.millionx.ai.data.num
import com.millionx.ai.data.str
import com.millionx.ai.ui.theme.MX
import com.millionx.ai.ui.vm.HealthVM
import com.millionx.ai.ui.vm.Load
import com.millionx.ai.ui.vm.ResourceVM
import kotlinx.coroutines.delay

/** Describes exactly what kind of trading the server is doing right now. */
fun modeLabel(mode: String): Pair<String, Color> = when (mode) {
    "live" -> "LIVE · REAL MONEY" to MX.Down
    "demo" -> "DEMO · Binance demo funds (not real)" to MX.Warn
    "paper" -> "PAPER · simulated fills on real prices" to MX.Info
    else -> "MODE UNKNOWN" to MX.Muted
}

/**
 * Always-visible ribbon: trading mode, engine/emergency state and a global
 * emergency stop reachable from every screen (no biometric delay by design).
 */
@Composable
fun ModeRibbon(vm: HealthVM) {
    val st by vm.state.collectAsState()
    var ask by remember { mutableStateOf(false) }
    val haptic = LocalHapticFeedback.current
    val h = (st as? Load.Ok<*>)?.data as? kotlinx.serialization.json.JsonElement
    val (label, color) = when {
        st is Load.Error -> "SERVER UNREACHABLE — data may be stale" to MX.Down
        h == null -> "connecting…" to MX.Muted
        h.bool("emergency_stop") -> "EMERGENCY STOP ACTIVE · ${modeLabel(h.str("mode")).first}" to MX.Down
        !h.bool("engine_running") -> "ENGINE STOPPED · ${modeLabel(h.str("mode")).first}" to MX.Down
        !h.bool("ok") && h.num("market_symbols") == 0.0 && h.str("mode").isNotEmpty() ->
            "NO MARKET DATA (exchange unreachable?) · ${modeLabel(h.str("mode")).first}" to MX.Warn
        else -> modeLabel(h.str("mode"))
    }
    Row(
        Modifier.fillMaxWidth().background(color.copy(alpha = 0.18f)).statusBarsPadding()
            .padding(start = 14.dp, end = 4.dp).testTag("mode_ribbon"),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(label, color = color, style = MaterialTheme.typography.labelSmall, fontWeight = FontWeight.Bold,
            modifier = Modifier.weight(1f))
        if (h != null && h["live_gate"].bool("valid")) {
            Text("live auth ✓", color = MX.Down, style = MaterialTheme.typography.labelSmall)
            Spacer(Modifier.width(6.dp))
        }
        IconButton(onClick = { haptic.performHapticFeedback(HapticFeedbackType.LongPress); ask = true },
            modifier = Modifier.testTag("ribbon_emergency")) {
            Icon(Icons.Filled.PowerSettingsNew, "Emergency stop", tint = MX.Down)
        }
    }
    if (ask) AlertDialog(
        onDismissRequest = { ask = false },
        title = { Text("Emergency stop") },
        text = { Text("Blocks new entries and closes every exchange position at market, verifying each is flat.") },
        confirmButton = { TextButton(onClick = { ask = false; vm.emergencyStop() }) { Text("STOP ALL", color = MX.Down) } },
        dismissButton = { TextButton(onClick = { ask = false }) { Text("Cancel") } },
    )
}

/** Warns when a screen shows data older than ~3 refresh intervals. */
@Composable
fun StaleBanner(vm: ResourceVM) {
    val last by vm.lastOk.collectAsState()
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
    LaunchedEffect(Unit) { while (true) { delay(5_000); now = System.currentTimeMillis() } }
    val age = if (last == 0L) 0L else (now - last) / 1000
    Banner("Data is ${age}s old — not refreshing (connection?)", MX.Warn,
        visible = last > 0 && (now - last) > 3 * vm.refreshInterval + 5_000)
}

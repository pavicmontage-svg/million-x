package com.millionx.ai.ui.components

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.animateColorAsState
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.PowerSettingsNew
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.millionx.ai.ui.theme.MX
import com.millionx.ai.ui.theme.Mono
import com.millionx.ai.ui.vm.Load

@Composable
fun MxCard(modifier: Modifier = Modifier, onClick: (() -> Unit)? = null, content: @Composable ColumnScope.() -> Unit) {
    val m = modifier
        .fillMaxWidth()
        .clip(RoundedCornerShape(18.dp))
        .background(MX.Surface)
        .border(1.dp, MX.Line, RoundedCornerShape(18.dp))
        .let { if (onClick != null) it.clickable { onClick() } else it }
        .padding(16.dp)
    Column(m, verticalArrangement = Arrangement.spacedBy(8.dp), content = content)
}

@Composable
fun SectionTitle(text: String, modifier: Modifier = Modifier, action: (@Composable RowScope.() -> Unit)? = null) {
    Row(modifier.fillMaxWidth().padding(top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(text.uppercase(), style = MaterialTheme.typography.labelSmall, color = MX.Muted, modifier = Modifier.weight(1f))
        action?.invoke(this)
    }
}

@Composable
fun Stat(label: String, value: String, modifier: Modifier = Modifier, color: Color = MX.Text, sub: String? = null) {
    Column(modifier) {
        Text(label, style = MaterialTheme.typography.labelSmall, color = MX.Muted)
        Text(value, style = MaterialTheme.typography.titleMedium, color = color, fontFamily = Mono,
            maxLines = 1, overflow = TextOverflow.Ellipsis)
        if (sub != null) Text(sub, style = MaterialTheme.typography.bodySmall, color = MX.Muted)
    }
}

@Composable
fun StatRow(vararg items: Triple<String, String, Color>) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        items.forEach { (l, v, c) -> Stat(l, v, Modifier.weight(1f), c) }
    }
}

@Composable
fun Chip(text: String, color: Color = MX.Info) {
    Box(
        Modifier.clip(RoundedCornerShape(50)).background(color.copy(alpha = 0.14f))
            .padding(horizontal = 10.dp, vertical = 4.dp)
    ) { Text(text, color = color, style = MaterialTheme.typography.labelSmall) }
}

@Composable
fun StatusDot(on: Boolean, label: String? = null) {
    val c by animateColorAsState(if (on) MX.Up else MX.Down, label = "dot")
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(8.dp).clip(CircleShape).background(c))
        if (label != null) {
            Spacer(Modifier.width(6.dp)); Text(label, style = MaterialTheme.typography.bodySmall, color = MX.Muted)
        }
    }
}

fun statusColor(s: String): Color = when (s.uppercase()) {
    "LIVE_ELIGIBLE", "ONLINE", "APPROVED", "EXECUTED", "FILLED", "OK", "PASS" -> MX.Up
    "PAPER", "BACKTEST_PASSED", "DETERMINISTIC", "RUNNING", "QUEUED" -> MX.Info
    "SUSPENDED", "WARNING", "AWAITING_OWNER_APPROVAL" -> MX.Warn
    "DISABLED", "OFFLINE", "VETOED", "REJECTED", "ERROR", "CRITICAL" -> MX.Down
    else -> MX.Muted
}

@Composable
fun KV(k: String, v: String, color: Color = MX.Text) {
    Row(Modifier.fillMaxWidth().padding(vertical = 2.dp)) {
        Text(k, color = MX.Muted, style = MaterialTheme.typography.bodySmall, modifier = Modifier.weight(1f))
        Text(v, color = color, style = MaterialTheme.typography.bodySmall, fontFamily = Mono)
    }
}

@Composable
fun <T> LoadBox(state: Load<T>, onRetry: () -> Unit, content: @Composable (T) -> Unit) {
    when (state) {
        is Load.Loading -> Box(Modifier.fillMaxWidth().padding(40.dp), contentAlignment = Alignment.Center) {
            CircularProgressIndicator(color = MX.Accent)
        }
        is Load.Error -> MxCard {
            Text("Error", color = MX.Down, fontWeight = FontWeight.SemiBold)
            Text(state.message, color = MX.Muted, style = MaterialTheme.typography.bodySmall)
            TextButton(onClick = onRetry) { Icon(Icons.Filled.Refresh, null); Text(" Retry") }
        }
        is Load.Ok -> content(state.data)
    }
}

/** Global emergency stop: confirms, then fires with a heavy haptic. */
@Composable
fun EmergencyButton(modifier: Modifier = Modifier, onConfirm: () -> Unit) {
    var ask by remember { mutableStateOf(false) }
    val haptic = LocalHapticFeedback.current
    Button(
        onClick = { haptic.performHapticFeedback(HapticFeedbackType.LongPress); ask = true },
        modifier = modifier.fillMaxWidth().height(56.dp),
        shape = RoundedCornerShape(16.dp),
        colors = ButtonDefaults.buttonColors(containerColor = MX.Down, contentColor = Color.White),
    ) {
        Icon(Icons.Filled.PowerSettingsNew, null)
        Spacer(Modifier.width(8.dp))
        Text("EMERGENCY STOP", fontWeight = FontWeight.Bold)
    }
    if (ask) {
        AlertDialog(
            onDismissRequest = { ask = false },
            title = { Text("Emergency stop") },
            text = { Text("Blocks all new entries, cancels pending approvals and closes every open position at market.") },
            confirmButton = {
                TextButton(onClick = {
                    ask = false; haptic.performHapticFeedback(HapticFeedbackType.LongPress); onConfirm()
                }) { Text("STOP ALL", color = MX.Down, fontWeight = FontWeight.Bold) }
            },
            dismissButton = { TextButton(onClick = { ask = false }) { Text("Cancel") } },
        )
    }
}

@Composable
fun ConfirmDialog(title: String, text: String, confirm: String, onConfirm: () -> Unit, onDismiss: () -> Unit) {
    AlertDialog(
        onDismissRequest = onDismiss, title = { Text(title) }, text = { Text(text) },
        confirmButton = { TextButton(onClick = { onDismiss(); onConfirm() }) { Text(confirm) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

@Composable
fun Banner(text: String, color: Color = MX.Warn, visible: Boolean = true) {
    AnimatedVisibility(visible) {
        Box(
            Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(color.copy(alpha = 0.12f))
                .border(1.dp, color.copy(alpha = 0.4f), RoundedCornerShape(12.dp)).padding(12.dp)
        ) { Text(text, color = color, style = MaterialTheme.typography.bodySmall) }
    }
}

@Composable
fun RefreshIcon(onClick: () -> Unit) {
    IconButton(onClick = onClick) { Icon(Icons.Filled.Refresh, "Refresh", tint = MX.Muted) }
}

val ScreenPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 8.dp, bottom = 120.dp)

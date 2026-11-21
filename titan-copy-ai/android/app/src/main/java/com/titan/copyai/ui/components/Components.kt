package com.titan.copyai.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
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
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.titan.copyai.ui.theme.Titan

@Composable
fun Panel(modifier: Modifier = Modifier, title: String? = null, trailing: (@Composable RowScope.() -> Unit)? = null,
          onClick: (() -> Unit)? = null, content: @Composable ColumnScope.() -> Unit) {
    Column(
        modifier
            .fillMaxWidth()
            .background(Titan.Surface, RoundedCornerShape(14.dp))
            .border(1.dp, Titan.Line, RoundedCornerShape(14.dp))
            .let { if (onClick != null) it.clickable(onClick = onClick) else it }
            .padding(14.dp),
    ) {
        if (title != null || trailing != null) {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                if (title != null) Text(title.uppercase(), style = MaterialTheme.typography.labelSmall, color = Titan.Muted,
                    modifier = Modifier.weight(1f))
                trailing?.invoke(this)
            }
            Spacer(Modifier.height(10.dp))
        }
        content()
    }
}

@Composable
fun Metric(label: String, value: String, modifier: Modifier = Modifier, color: Color = Titan.Text, sub: String? = null) {
    Column(modifier) {
        Text(label, style = MaterialTheme.typography.labelSmall, color = Titan.Muted, maxLines = 1)
        Text(value, style = MaterialTheme.typography.titleMedium,
            color = if (value == UNKNOWN) Titan.Muted else color, maxLines = 1, overflow = TextOverflow.Ellipsis)
        if (sub != null) Text(sub, style = MaterialTheme.typography.bodySmall, color = Titan.Muted, maxLines = 1)
    }
}

@Composable
fun MetricRow(vararg items: Triple<String, String, Color>) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        items.forEach { (l, v, c) -> Metric(l, v, Modifier.weight(1f), c) }
    }
}

fun pnlColor(v: Double?): Color = when {
    v == null -> Titan.Muted
    v > 0 -> Titan.Up
    v < 0 -> Titan.Down
    else -> Titan.Text
}

@Composable
fun Chip(text: String, color: Color, modifier: Modifier = Modifier) {
    Text(text.uppercase(), modifier
        .background(color.copy(alpha = 0.14f), RoundedCornerShape(6.dp))
        .border(1.dp, color.copy(alpha = 0.45f), RoundedCornerShape(6.dp))
        .padding(horizontal = 7.dp, vertical = 2.dp),
        style = MaterialTheme.typography.labelSmall, color = color, maxLines = 1)
}

@Composable
fun SimulatedBadge(simulated: Boolean) {
    if (simulated) Chip("SIMULATED", Titan.Sim)
}

@Composable
fun StatusDot(ok: Boolean?, label: String) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(8.dp).background(when (ok) { true -> Titan.Up; false -> Titan.Down; null -> Titan.Muted }, CircleShape))
        Spacer(Modifier.width(6.dp))
        Text(label, style = MaterialTheme.typography.labelMedium, color = Titan.Muted)
    }
}

fun statusColor(status: String): Color = when (status) {
    "active", "approved", "executed", "filled", "open", "eligible", "ok" -> Titan.Up
    "paused", "monitoring", "candidate", "partially_executed", "watch" -> Titan.Warn
    "halted_daily", "halted_weekly", "review_required", "rejected", "failed", "suspended", "unknown" -> Titan.Down
    else -> Titan.Muted
}

@Composable
fun UsageBar(label: String, usedPct: Double?, modifier: Modifier = Modifier) {
    Column(modifier) {
        Row {
            Text(label, style = MaterialTheme.typography.labelSmall, color = Titan.Muted, modifier = Modifier.weight(1f))
            Text(Fmt.pct(usedPct, 0), style = MaterialTheme.typography.labelSmall,
                color = if ((usedPct ?: 0.0) >= 80) Titan.Down else Titan.Text)
        }
        Spacer(Modifier.height(4.dp))
        LinearProgressIndicator(
            progress = { ((usedPct ?: 0.0) / 100).toFloat().coerceIn(0f, 1f) },
            modifier = Modifier.fillMaxWidth().height(6.dp),
            color = when { (usedPct ?: 0.0) >= 80 -> Titan.Down; (usedPct ?: 0.0) >= 50 -> Titan.Warn; else -> Titan.Accent },
            trackColor = Titan.Surface2,
        )
    }
}

@Composable
fun KV(k: String, v: String, color: Color = Titan.Text) {
    Row(Modifier.fillMaxWidth().padding(vertical = 3.dp)) {
        Text(k, style = MaterialTheme.typography.bodyMedium, color = Titan.Muted, modifier = Modifier.weight(1f))
        Text(v, style = MaterialTheme.typography.bodyMedium, color = if (v == UNKNOWN) Titan.Muted else color,
            fontWeight = FontWeight.Medium)
    }
}

@Composable
fun Divider() = HorizontalDivider(color = Titan.Line, thickness = 1.dp, modifier = Modifier.padding(vertical = 8.dp))

@Composable
fun Loading() = Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
    CircularProgressIndicator(color = Titan.Accent)
}

@Composable
fun ErrorBox(msg: String, onRetry: (() -> Unit)? = null) {
    Panel(title = "Error") {
        Text(msg, color = Titan.Down, style = MaterialTheme.typography.bodyMedium)
        if (onRetry != null) TextButton(onClick = onRetry) { Text("Retry") }
    }
}

@Composable
fun StaleBanner(updatedAt: Long, error: String?) {
    Panel {
        Text("OFFLINE - showing cached data from ${java.text.DateFormat.getDateTimeInstance().format(java.util.Date(updatedAt))}",
            color = Titan.Warn, style = MaterialTheme.typography.bodySmall)
        if (error != null) Text(error, color = Titan.Muted, style = MaterialTheme.typography.bodySmall)
    }
}

@Composable
fun Empty(text: String) = Text(text, color = Titan.Muted, style = MaterialTheme.typography.bodyMedium,
    modifier = Modifier.padding(vertical = 12.dp))

/** Dangerous actions require typing an exact confirmation phrase. */
@Composable
fun ConfirmPhraseDialog(title: String, body: String, phrase: String, confirmLabel: String,
                        onDismiss: () -> Unit, onConfirm: (String) -> Unit) {
    var text by remember { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = Titan.Surface2,
        title = { Text(title) },
        text = {
            Column {
                Text(body, style = MaterialTheme.typography.bodyMedium)
                Spacer(Modifier.height(8.dp))
                Text("Type: $phrase", style = MaterialTheme.typography.labelMedium, color = Titan.Gold)
                OutlinedTextField(text, { text = it }, singleLine = true, modifier = Modifier.fillMaxWidth())
            }
        },
        confirmButton = {
            Button(onClick = { onConfirm(text) }, enabled = text == phrase,
                colors = ButtonDefaults.buttonColors(containerColor = Titan.Down)) { Text(confirmLabel) }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

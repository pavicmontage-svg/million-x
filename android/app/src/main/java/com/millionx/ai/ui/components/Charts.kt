package com.millionx.ai.ui.components

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.millionx.ai.ui.theme.MX

/** Animated area/line chart for equity curves. */
@Composable
fun LineChart(values: List<Double>, modifier: Modifier = Modifier, height: Dp = 160.dp, color: Color = MX.Accent,
              baseline: Double? = null) {
    if (values.size < 2) {
        Box(modifier.fillMaxWidth().height(height), contentAlignment = Alignment.Center) {
            Text("Not enough data yet", color = MX.Muted, style = MaterialTheme.typography.bodySmall)
        }
        return
    }
    val anim = remember(values.size) { Animatable(0f) }
    LaunchedEffect(values.size) { anim.animateTo(1f, tween(700)) }
    Canvas(modifier.fillMaxWidth().height(height)) {
        val lo = minOf(values.min(), baseline ?: values.min())
        val hi = maxOf(values.max(), baseline ?: values.max())
        val span = (hi - lo).takeIf { it > 0 } ?: 1.0
        val n = values.size
        fun x(i: Int) = size.width * i / (n - 1)
        fun y(v: Double) = (size.height - (v - lo) / span * size.height).toFloat()
        val upto = (n * anim.value).toInt().coerceIn(2, n)
        val p = Path().apply {
            moveTo(0f, y(values[0]))
            for (i in 1 until upto) lineTo(x(i), y(values[i]))
        }
        val area = Path().apply {
            addPath(p); lineTo(x(upto - 1), size.height); lineTo(0f, size.height); close()
        }
        drawPath(area, Brush.verticalGradient(listOf(color.copy(alpha = 0.28f), Color.Transparent)))
        drawPath(p, color, style = Stroke(width = 2.5.dp.toPx(), cap = StrokeCap.Round))
        if (baseline != null) {
            val yb = y(baseline)
            drawLine(MX.Muted.copy(alpha = 0.5f), Offset(0f, yb), Offset(size.width, yb), strokeWidth = 1f)
        }
    }
}

/** Horizontal bars (e.g. strategy weights or R per bot). */
@Composable
fun BarList(items: List<Pair<String, Double>>, max: Double? = null, format: (Double) -> String = { "%.2f".format(it) }) {
    val m = max ?: (items.maxOfOrNull { kotlin.math.abs(it.second) } ?: 1.0).coerceAtLeast(1e-9)
    items.forEach { (label, v) ->
        androidx.compose.foundation.layout.Row(verticalAlignment = Alignment.CenterVertically) {
            Text(label, modifier = Modifier.fillMaxWidth(0.38f), style = MaterialTheme.typography.bodySmall, color = MX.Muted,
                maxLines = 1)
            Canvas(Modifier.fillMaxWidth(0.75f).height(10.dp)) {
                val w = (kotlin.math.abs(v) / m).toFloat().coerceIn(0f, 1f) * size.width
                drawRoundRect(MX.Surface2, size = size)
                drawRoundRect(if (v >= 0) MX.Up else MX.Down, size = androidx.compose.ui.geometry.Size(w, size.height))
            }
            Text("  " + format(v), style = MaterialTheme.typography.bodySmall)
        }
    }
}

/** Progress ring for the 1M EUR goal (log scale = doublings). */
@Composable
fun GoalRing(progress: Float, modifier: Modifier = Modifier, label: String) {
    Box(modifier, contentAlignment = Alignment.Center) {
        Canvas(Modifier.fillMaxWidth().height(140.dp)) {
            val d = minOf(size.width, size.height) * 0.9f
            val tl = Offset((size.width - d) / 2, (size.height - d) / 2)
            val s = androidx.compose.ui.geometry.Size(d, d)
            drawArc(MX.Surface2, 135f, 270f, false, tl, s, style = Stroke(14.dp.toPx(), cap = StrokeCap.Round))
            drawArc(Brush.sweepGradient(listOf(MX.Accent, MX.Info, MX.Accent)), 135f, 270f * progress.coerceIn(0f, 1f),
                false, tl, s, style = Stroke(14.dp.toPx(), cap = StrokeCap.Round))
        }
        Text(label, style = MaterialTheme.typography.titleMedium)
    }
}

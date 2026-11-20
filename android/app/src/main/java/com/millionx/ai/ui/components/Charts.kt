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

/** Distinct colours for multi-series charts (bots). */
val SERIES_COLORS = listOf(
    Color(0xFF00E0A4), Color(0xFF5AA9FF), Color(0xFFFFB547), Color(0xFFA78BFA), Color(0xFFFF5A6E),
    Color(0xFF4DD0E1), Color(0xFFF06292), Color(0xFFAED581), Color(0xFFFFD54F), Color(0xFF90A4AE),
)

data class Candle(val t: Long, val o: Double, val h: Double, val l: Double, val c: Double)
data class ChartMarker(val t: Long, val price: Double, val long: Boolean, val accepted: Boolean)
data class ChartLevel(val price: Double, val color: Color, val dashed: Boolean)

/** Candlestick chart with bot-signal markers (▲ long / ▼ short) and open-position levels. */
@Composable
fun CandleChart(candles: List<Candle>, markers: List<ChartMarker>, levels: List<ChartLevel>,
                modifier: Modifier = Modifier, height: Dp = 260.dp) {
    if (candles.size < 2) {
        Box(modifier.fillMaxWidth().height(height), contentAlignment = Alignment.Center) {
            Text("Waiting for market data…", color = MX.Muted, style = MaterialTheme.typography.bodySmall)
        }
        return
    }
    Canvas(modifier.fillMaxWidth().height(height)) {
        val visibleLevels = levels.map { it.price }
        val lo0 = candles.minOf { it.l }
        val hi0 = candles.maxOf { it.h }
        // include levels only if they are reasonably near (keep the candles readable)
        val near = visibleLevels.filter { it > lo0 - (hi0 - lo0) && it < hi0 + (hi0 - lo0) }
        val lo = minOf(lo0, near.minOrNull() ?: lo0)
        val hi = maxOf(hi0, near.maxOrNull() ?: hi0)
        val pad = (hi - lo).coerceAtLeast(hi * 1e-6) * 0.06
        val top = hi + pad
        val bot = lo - pad
        val span = top - bot
        val n = candles.size
        val slot = size.width / n
        val body = (slot * 0.62f).coerceAtLeast(1.5f)
        fun y(p: Double) = ((top - p) / span * size.height).toFloat()
        val t0 = candles.first().t
        val stepMs = ((candles.last().t - t0) / (n - 1)).coerceAtLeast(1)
        // grid
        for (k in 1..3) {
            val yy = size.height * k / 4
            drawLine(MX.Line, Offset(0f, yy), Offset(size.width, yy), strokeWidth = 1f)
        }
        candles.forEachIndexed { i, k ->
            val cx = slot * i + slot / 2
            val up = k.c >= k.o
            val col = if (up) MX.Up else MX.Down
            drawLine(col, Offset(cx, y(k.h)), Offset(cx, y(k.l)), strokeWidth = 1.2f)
            val yt = y(maxOf(k.o, k.c))
            val yb = y(minOf(k.o, k.c))
            drawRect(col, topLeft = Offset(cx - body / 2, yt),
                size = androidx.compose.ui.geometry.Size(body, (yb - yt).coerceAtLeast(1.2f)))
        }
        levels.forEach { lv ->
            if (lv.price in bot..top) {
                val yy = y(lv.price)
                drawLine(lv.color, Offset(0f, yy), Offset(size.width, yy), strokeWidth = 2f,
                    pathEffect = if (lv.dashed) androidx.compose.ui.graphics.PathEffect.dashPathEffect(floatArrayOf(10f, 8f)) else null)
            }
        }
        markers.forEach { m ->
            // signal ts is the close of the bar -> index of the bar that produced it
            val idx = (((m.t - t0) / stepMs) - 1).toInt()
            if (idx < 0 || idx >= n) return@forEach
            val cx = slot * idx + slot / 2
            val col = if (m.long) MX.Up else MX.Down
            val a = if (m.accepted) 1f else 0.45f
            val s = 9.dp.toPx()
            val k = candles[idx]
            val path = Path()
            if (m.long) {
                val yy = y(k.l) + 4.dp.toPx()
                path.moveTo(cx, yy); path.lineTo(cx - s / 2, yy + s); path.lineTo(cx + s / 2, yy + s); path.close()
            } else {
                val yy = y(k.h) - 4.dp.toPx()
                path.moveTo(cx, yy); path.lineTo(cx - s / 2, yy - s); path.lineTo(cx + s / 2, yy - s); path.close()
            }
            drawPath(path, col.copy(alpha = a))
        }
        // last price line
        val last = candles.last().c
        drawLine(MX.Info.copy(alpha = 0.6f), Offset(0f, y(last)), Offset(size.width, y(last)), strokeWidth = 1f,
            pathEffect = androidx.compose.ui.graphics.PathEffect.dashPathEffect(floatArrayOf(4f, 6f)))
    }
}

/** Several cumulative curves on a shared time axis (strategy competition). */
@Composable
fun MultiLineChart(series: List<Pair<Color, List<Pair<Long, Double>>>>, modifier: Modifier = Modifier,
                   height: Dp = 200.dp) {
    val pts = series.flatMap { it.second }
    if (pts.size < 2) {
        Box(modifier.fillMaxWidth().height(height), contentAlignment = Alignment.Center) {
            Text("No closed shadow trades yet — curves appear after the first exits",
                color = MX.Muted, style = MaterialTheme.typography.bodySmall)
        }
        return
    }
    Canvas(modifier.fillMaxWidth().height(height)) {
        val t0 = pts.minOf { it.first }
        val t1 = pts.maxOf { it.first }.coerceAtLeast(t0 + 1)
        val lo = minOf(0.0, pts.minOf { it.second })
        val hi = maxOf(0.0, pts.maxOf { it.second })
        val span = (hi - lo).takeIf { it > 0 } ?: 1.0
        fun x(t: Long) = ((t - t0).toDouble() / (t1 - t0) * size.width).toFloat()
        fun y(v: Double) = (size.height - (v - lo) / span * size.height).toFloat()
        drawLine(MX.Muted.copy(alpha = 0.6f), Offset(0f, y(0.0)), Offset(size.width, y(0.0)), strokeWidth = 1f)
        series.forEach { (col, s) ->
            if (s.isEmpty()) return@forEach
            val p = Path().apply {
                moveTo(x(s.first().first), y(0.0))
                s.forEach { (t, v) -> lineTo(x(t), y(v)) }
                lineTo(size.width, y(s.last().second))
            }
            drawPath(p, col, style = Stroke(width = 2.dp.toPx(), cap = StrokeCap.Round))
        }
    }
}

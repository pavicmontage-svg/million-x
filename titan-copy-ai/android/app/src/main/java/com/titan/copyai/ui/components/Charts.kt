package com.titan.copyai.ui.components

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.titan.copyai.ui.theme.Titan

data class ChartPoint(val x: Long, val y: Double, val label: String)

/**
 * Interactive line chart: drag or tap to scrub; shows the value and timestamp of the nearest point.
 * Draws a dashed baseline (e.g. initial capital) when provided.
 */
@Composable
fun LineChart(points: List<ChartPoint>, modifier: Modifier = Modifier, height: Dp = 180.dp, color: Color = Titan.Accent,
              baseline: Double? = null, valueFmt: (Double) -> String = { Fmt.num(it) }) {
    if (points.size < 2) {
        Text("Not enough data for a chart yet", color = Titan.Muted, style = MaterialTheme.typography.bodySmall)
        return
    }
    var sel by remember(points) { mutableStateOf<Int?>(null) }
    val minY = minOf(points.minOf { it.y }, baseline ?: Double.MAX_VALUE)
    val maxY = maxOf(points.maxOf { it.y }, baseline ?: -Double.MAX_VALUE)
    val span = (maxY - minY).takeIf { it > 0 } ?: 1.0
    val up = points.last().y >= (baseline ?: points.first().y)
    val line = if (color == Titan.Accent) (if (up) Titan.Up else Titan.Down) else color
    Column(modifier.fillMaxWidth()) {
        val shown = sel?.let { points[it] } ?: points.last()
        Text("${valueFmt(shown.y)}   ${shown.label}", style = MaterialTheme.typography.bodySmall, color = Titan.Muted)
        Box(Modifier.fillMaxWidth().height(height)) {
            Canvas(
                Modifier.fillMaxWidth().height(height)
                    .pointerInput(points) {
                        detectTapGestures { o -> sel = nearest(o.x, size.width.toFloat(), points.size) }
                    }
                    .pointerInput(points) {
                        detectDragGestures(onDragEnd = { }, onDrag = { change, _ ->
                            sel = nearest(change.position.x, size.width.toFloat(), points.size)
                        })
                    },
            ) {
                val w = size.width
                val h = size.height
                fun px(i: Int) = i * w / (points.size - 1)
                fun py(v: Double) = (h - ((v - minY) / span * (h * 0.9) + h * 0.05)).toFloat()
                for (g in 1..3) {
                    val y = h * g / 4
                    drawLine(Titan.Line, Offset(0f, y), Offset(w, y), strokeWidth = 1f)
                }
                baseline?.let {
                    drawLine(Titan.Muted, Offset(0f, py(it)), Offset(w, py(it)), strokeWidth = 1.5f,
                        pathEffect = PathEffect.dashPathEffect(floatArrayOf(8f, 8f)))
                }
                val path = Path()
                points.forEachIndexed { i, p -> if (i == 0) path.moveTo(px(i), py(p.y)) else path.lineTo(px(i), py(p.y)) }
                val fill = Path().apply { addPath(path); lineTo(w, h); lineTo(0f, h); close() }
                drawPath(fill, Brush.verticalGradient(listOf(line.copy(alpha = 0.25f), Color.Transparent)))
                drawPath(path, line, style = Stroke(width = 3f, cap = StrokeCap.Round))
                sel?.let { i ->
                    val x = px(i)
                    drawLine(Titan.Gold, Offset(x, 0f), Offset(x, h), strokeWidth = 1.5f)
                    drawCircle(Titan.Gold, 6f, Offset(x, py(points[i].y)))
                }
            }
        }
    }
}

private fun nearest(x: Float, width: Float, n: Int): Int = ((x / width) * (n - 1)).toInt().coerceIn(0, n - 1)

/** Compact horizontal bar for score components (0..100). */
@Composable
fun ScoreBar(value: Double?, modifier: Modifier = Modifier) {
    Canvas(modifier.fillMaxWidth().height(8.dp)) {
        drawRoundRect(Titan.Surface2, cornerRadius = androidx.compose.ui.geometry.CornerRadius(8f))
        if (value != null) {
            val c = when { value >= 70 -> Titan.Up; value >= 45 -> Titan.Warn; else -> Titan.Down }
            drawRoundRect(c, size = size.copy(width = (size.width * (value / 100).toFloat()).coerceAtLeast(4f)),
                cornerRadius = androidx.compose.ui.geometry.CornerRadius(8f))
        }
    }
}

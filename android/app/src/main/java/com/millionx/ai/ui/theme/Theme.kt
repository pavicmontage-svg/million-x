package com.millionx.ai.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

object MX {
    val Bg = Color(0xFF07090D)
    val Surface = Color(0xFF0F131A)
    val Surface2 = Color(0xFF161B24)
    val Line = Color(0xFF232A36)
    val Text = Color(0xFFE8ECF2)
    val Muted = Color(0xFF8792A2)
    val Accent = Color(0xFF00E0A4)
    val Up = Color(0xFF22D39A)
    val Down = Color(0xFFFF5A6E)
    val Warn = Color(0xFFFFB547)
    val Info = Color(0xFF5AA9FF)
    val Purple = Color(0xFFA78BFA)
}

private val scheme = darkColorScheme(
    primary = MX.Accent, onPrimary = Color(0xFF00241A), secondary = MX.Info, tertiary = MX.Purple,
    background = MX.Bg, onBackground = MX.Text, surface = MX.Surface, onSurface = MX.Text,
    surfaceVariant = MX.Surface2, onSurfaceVariant = MX.Muted, outline = MX.Line, error = MX.Down,
    surfaceContainer = MX.Surface, surfaceContainerHigh = MX.Surface2, surfaceContainerLow = MX.Surface,
)

val Mono = FontFamily.Monospace

private val type = Typography(
    headlineLarge = TextStyle(fontWeight = FontWeight.Bold, fontSize = 30.sp, letterSpacing = (-0.5).sp),
    headlineSmall = TextStyle(fontWeight = FontWeight.SemiBold, fontSize = 22.sp),
    titleMedium = TextStyle(fontWeight = FontWeight.SemiBold, fontSize = 16.sp),
    bodyMedium = TextStyle(fontSize = 14.sp),
    bodySmall = TextStyle(fontSize = 12.sp),
    labelSmall = TextStyle(fontSize = 11.sp, letterSpacing = 0.6.sp, fontWeight = FontWeight.Medium),
)

@Composable
fun MillionXTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = scheme, typography = type, content = content)
}

fun pnlColor(v: Double?): Color = when {
    v == null || v.isNaN() -> MX.Muted
    v > 0 -> MX.Up
    v < 0 -> MX.Down
    else -> MX.Muted
}

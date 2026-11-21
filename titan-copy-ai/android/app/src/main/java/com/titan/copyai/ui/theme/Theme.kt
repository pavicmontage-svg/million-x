package com.titan.copyai.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

object Titan {
    val Bg = Color(0xFF0A0E13)
    val Surface = Color(0xFF111821)
    val Surface2 = Color(0xFF17202B)
    val Line = Color(0xFF243140)
    val Text = Color(0xFFE6EDF3)
    val Muted = Color(0xFF8B98A9)
    val Accent = Color(0xFF2BD4C4)     // teal: interactive
    val Gold = Color(0xFFD9B25F)       // premium highlights
    val Up = Color(0xFF2ECC8F)
    val Down = Color(0xFFFF5D6C)
    val Warn = Color(0xFFF5A524)
    val Sim = Color(0xFF8E7CF0)        // SIMULATED data badge
}

private val scheme = darkColorScheme(
    primary = Titan.Accent, onPrimary = Color(0xFF00201D), secondary = Titan.Gold, onSecondary = Color(0xFF241A00),
    background = Titan.Bg, onBackground = Titan.Text, surface = Titan.Surface, onSurface = Titan.Text,
    surfaceVariant = Titan.Surface2, onSurfaceVariant = Titan.Muted, outline = Titan.Line,
    error = Titan.Down, onError = Color.White, surfaceContainer = Titan.Surface, surfaceContainerHigh = Titan.Surface2,
    surfaceContainerLow = Titan.Surface, surfaceContainerHighest = Titan.Surface2,
)

/** Numbers use tabular figures so columns of prices/P&L align like a terminal. */
val Num = TextStyle(fontFeatureSettings = "tnum", fontFamily = FontFamily.Default)

private val typography = Typography(
    headlineLarge = TextStyle(fontSize = 30.sp, fontWeight = FontWeight.SemiBold, letterSpacing = (-0.5).sp, fontFeatureSettings = "tnum"),
    headlineMedium = TextStyle(fontSize = 24.sp, fontWeight = FontWeight.SemiBold, fontFeatureSettings = "tnum"),
    titleLarge = TextStyle(fontSize = 20.sp, fontWeight = FontWeight.SemiBold),
    titleMedium = TextStyle(fontSize = 16.sp, fontWeight = FontWeight.SemiBold, letterSpacing = 0.1.sp),
    titleSmall = TextStyle(fontSize = 13.sp, fontWeight = FontWeight.Medium, letterSpacing = 0.4.sp),
    bodyLarge = TextStyle(fontSize = 15.sp, lineHeight = 21.sp),
    bodyMedium = TextStyle(fontSize = 13.sp, lineHeight = 18.sp, fontFeatureSettings = "tnum"),
    bodySmall = TextStyle(fontSize = 12.sp, lineHeight = 16.sp, fontFeatureSettings = "tnum"),
    labelSmall = TextStyle(fontSize = 11.sp, fontWeight = FontWeight.Medium, letterSpacing = 0.6.sp),
    labelMedium = TextStyle(fontSize = 12.sp, fontWeight = FontWeight.Medium, letterSpacing = 0.3.sp),
)

@Composable
fun TitanTheme(content: @Composable () -> Unit) =
    MaterialTheme(colorScheme = scheme, typography = typography, content = content)

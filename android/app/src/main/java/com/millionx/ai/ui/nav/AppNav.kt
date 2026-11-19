package com.millionx.ai.ui.nav

import android.content.Context
import android.os.VibrationEffect
import android.os.Vibrator
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Apps
import androidx.compose.material.icons.filled.Groups
import androidx.compose.material.icons.filled.Insights
import androidx.compose.material.icons.filled.Shield
import androidx.compose.material.icons.filled.SmartToy
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import com.millionx.ai.data.get
import com.millionx.ai.data.str
import com.millionx.ai.ui.screens.AdminScreen
import com.millionx.ai.ui.screens.AgentDetailScreen
import com.millionx.ai.ui.screens.AuditScreen
import com.millionx.ai.ui.screens.BoardroomScreen
import com.millionx.ai.ui.screens.BotDetailScreen
import com.millionx.ai.ui.screens.BotsScreen
import com.millionx.ai.ui.screens.BrainsScreen
import com.millionx.ai.ui.screens.DashboardScreen
import com.millionx.ai.ui.screens.ExecutionScreen
import com.millionx.ai.ui.screens.JobDetailScreen
import com.millionx.ai.ui.screens.MoreScreen
import com.millionx.ai.ui.screens.NotificationsScreen
import com.millionx.ai.ui.screens.PortfolioScreen
import com.millionx.ai.ui.screens.QuantLabScreen
import com.millionx.ai.ui.screens.RiskScreen
import com.millionx.ai.ui.screens.StrategyCenterScreen
import com.millionx.ai.ui.screens.TradesScreen
import com.millionx.ai.ui.theme.MX
import com.millionx.ai.ui.vm.LiveVM
import com.millionx.ai.ui.vm.HealthVM
import com.millionx.ai.ui.components.ModeRibbon

private data class Tab(val route: String, val label: String, val icon: androidx.compose.ui.graphics.vector.ImageVector)

private val TABS = listOf(
    Tab("dashboard", "Home", Icons.Filled.Insights),
    Tab("bots", "Bots", Icons.Filled.SmartToy),
    Tab("boardroom", "Board", Icons.Filled.Groups),
    Tab("risk", "Risk", Icons.Filled.Shield),
    Tab("more", "More", Icons.Filled.Apps),
)

@Composable
fun AppNav(onLogout: () -> Unit) {
    val nav = rememberNavController()
    val live: LiveVM = viewModel()
    val health: HealthVM = viewModel()
    val snack = remember { SnackbarHostState() }
    LaunchedEffect(Unit) { health.messages.collect { snack.showSnackbar(it) } }
    val ctx = LocalContext.current
    // Haptic + snackbar for important live events (trades, approvals, risk alerts)
    LaunchedEffect(Unit) {
        live.alerts.collect { e ->
            val level = e.data.str("level", if (e.type == "approval_request") "warning" else "info")
            if (level == "critical" || level == "warning" || e.type == "approval_request") vibrate(ctx, level == "critical")
            val msg = when (e.type) {
                "alert" -> e.data.str("title")
                "approval_request" -> "Approval needed: ${e.data["signal"].str("side")} ${e.data["signal"].str("symbol")}"
                "trade" -> "Closed ${e.data.str("symbol")}: ${e.data.str("net_pnl").take(8)}"
                "position_opened" -> "Opened ${e.data.str("side")} ${e.data.str("symbol")}"
                else -> e.type
            }
            snack.showSnackbar(msg)
        }
    }
    val entry by nav.currentBackStackEntryAsState()
    val current = entry?.destination?.route
    Scaffold(
        containerColor = MX.Bg,
        snackbarHost = { SnackbarHost(snack) },
        topBar = { ModeRibbon(health) },
        bottomBar = {
            NavigationBar(containerColor = MX.Surface) {
                TABS.forEach { t ->
                    NavigationBarItem(
                        selected = current == t.route,
                        onClick = {
                            nav.navigate(t.route) {
                                popUpTo(nav.graph.findStartDestination().id) { saveState = true }
                                launchSingleTop = true
                                restoreState = true
                            }
                        },
                        icon = { Icon(t.icon, t.label) },
                        label = { Text(t.label) },
                        colors = NavigationBarItemDefaults.colors(selectedIconColor = MX.Accent, selectedTextColor = MX.Accent,
                            indicatorColor = MX.Surface2),
                    )
                }
            }
        },
    ) { pad ->
        NavHost(nav, startDestination = "dashboard", modifier = Modifier.padding(pad)) {
            composable("dashboard") { DashboardScreen(nav) }
            composable("bots") { BotsScreen(nav) }
            composable("bot/{id}") { BotDetailScreen(nav, it.arguments?.getString("id") ?: "") }
            composable("boardroom") { BoardroomScreen(nav) }
            composable("brains") { BrainsScreen(nav) }
            composable("agent/{id}") { AgentDetailScreen(nav, it.arguments?.getString("id") ?: "") }
            composable("risk") { RiskScreen(nav) }
            composable("portfolio") { PortfolioScreen(nav) }
            composable("strategies") { StrategyCenterScreen(nav) }
            composable("quant") { QuantLabScreen(nav) }
            composable("job/{id}") { JobDetailScreen(nav, it.arguments?.getString("id") ?: "") }
            composable("execution") { ExecutionScreen(nav) }
            composable("trades") { TradesScreen(nav) }
            composable("notifications") { NotificationsScreen(nav) }
            composable("audit") { AuditScreen(nav) }
            composable("admin") { AdminScreen(nav, onLogout) }
            composable("more") { MoreScreen(nav) }
        }
    }
}

@Suppress("DEPRECATION")
private fun vibrate(ctx: Context, strong: Boolean) {
    val v = ctx.getSystemService(Vibrator::class.java) ?: return
    if (!v.hasVibrator()) return
    v.vibrate(VibrationEffect.createPredefined(if (strong) VibrationEffect.EFFECT_HEAVY_CLICK else VibrationEffect.EFFECT_CLICK))
}

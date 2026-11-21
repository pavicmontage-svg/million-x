package com.titan.copyai.ui.nav

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.List
import androidx.compose.material.icons.filled.AccountBalance
import androidx.compose.material.icons.filled.Analytics
import androidx.compose.material.icons.filled.CurrencyBitcoin
import androidx.compose.material.icons.filled.CurrencyExchange
import androidx.compose.material.icons.filled.Dashboard
import androidx.compose.material.icons.filled.Groups
import androidx.compose.material.icons.filled.History
import androidx.compose.material.icons.filled.Key
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material.icons.filled.Notifications
import androidx.compose.material.icons.filled.PieChart
import androidx.compose.material.icons.filled.Psychology
import androidx.compose.material.icons.filled.ReceiptLong
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Shield
import androidx.compose.material.icons.filled.ShowChart
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.BadgedBox
import androidx.compose.material3.Badge
import androidx.compose.material3.DrawerValue
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalDrawerSheet
import androidx.compose.material3.ModalNavigationDrawer
import androidx.compose.material3.NavigationDrawerItem
import androidx.compose.material3.NavigationDrawerItemDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.rememberDrawerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.unit.dp
import androidx.navigation.NavHostController
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import com.titan.copyai.ui.components.StatusDot
import com.titan.copyai.ui.screens.AiCenterScreen
import com.titan.copyai.ui.screens.AnalyticsScreen
import com.titan.copyai.ui.screens.AuditScreen
import com.titan.copyai.ui.screens.BinanceConnectionsScreen
import com.titan.copyai.ui.screens.BrokerConnectionsScreen
import com.titan.copyai.ui.screens.CopyPortfolioScreen
import com.titan.copyai.ui.screens.DashboardScreen
import com.titan.copyai.ui.screens.EmergencyScreen
import com.titan.copyai.ui.screens.HistoryScreen
import com.titan.copyai.ui.screens.MarketDashboardScreen
import com.titan.copyai.ui.screens.MarketplaceScreen
import com.titan.copyai.ui.screens.NotificationsScreen
import com.titan.copyai.ui.screens.PositionsScreen
import com.titan.copyai.ui.screens.RiskScreen
import com.titan.copyai.ui.screens.SettingsScreen
import com.titan.copyai.ui.screens.TraderDetailScreen
import com.titan.copyai.ui.theme.Titan
import com.titan.copyai.ui.vm.AppVm
import kotlinx.coroutines.launch

data class Dest(val route: String, val title: String, val icon: ImageVector, val group: String)

val DESTS = listOf(
    Dest("dashboard", "Main Dashboard", Icons.Filled.Dashboard, "Overview"),
    Dest("forex", "Forex Dashboard", Icons.Filled.CurrencyExchange, "Overview"),
    Dest("binance", "Binance Dashboard", Icons.Filled.CurrencyBitcoin, "Overview"),
    Dest("traders", "Trader Marketplace", Icons.Filled.Groups, "Traders"),
    Dest("portfolio", "Copy Portfolio", Icons.Filled.PieChart, "Traders"),
    Dest("analytics", "Quantitative Analytics", Icons.Filled.Analytics, "Traders"),
    Dest("positions", "Open Positions", Icons.Filled.ShowChart, "Trading"),
    Dest("history", "Trade History", Icons.Filled.History, "Trading"),
    Dest("risk", "Risk Management", Icons.Filled.Shield, "Control"),
    Dest("ai", "AI Intelligence Center", Icons.Filled.Psychology, "Control"),
    Dest("emergency", "Emergency Controls", Icons.Filled.Warning, "Control"),
    Dest("brokers", "Broker Connections", Icons.Filled.AccountBalance, "Connections"),
    Dest("binance_api", "Binance API Connections", Icons.Filled.Key, "Connections"),
    Dest("notifications", "Notifications", Icons.Filled.Notifications, "System"),
    Dest("settings", "Settings", Icons.Filled.Settings, "System"),
    Dest("audit", "Audit Logs", Icons.Filled.ReceiptLong, "System"),
)

class Ctx(val app: AppVm, val nav: NavHostController, val snack: (String) -> Unit, val tick: Long)

val LocalCtx = staticCompositionLocalOf<Ctx> { error("no ctx") }

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AppNav(app: AppVm, startRoute: String? = null) {
    val nav = rememberNavController()
    val drawer = rememberDrawerState(DrawerValue.Closed)
    val scope = rememberCoroutineScope()
    val snackHost = remember { SnackbarHostState() }
    val live by app.liveConnected.collectAsState()
    val tick by app.refreshTick.collectAsState()
    val entry by nav.currentBackStackEntryAsState()
    val route = entry?.destination?.route ?: "dashboard"
    val title = DESTS.firstOrNull { it.route == route.substringBefore("/") }?.title ?: "Trader Details"
    LaunchedEffect(startRoute) { if (startRoute != null) nav.navigate(startRoute) }

    ModalNavigationDrawer(
        drawerState = drawer,
        drawerContent = {
            ModalDrawerSheet(drawerContainerColor = Titan.Surface) {
                Column(Modifier.verticalScroll(rememberScrollState()).padding(12.dp)) {
                    Text("TITAN COPY AI", style = MaterialTheme.typography.titleLarge, color = Titan.Gold,
                        modifier = Modifier.padding(12.dp))
                    var last = ""
                    DESTS.forEach { d ->
                        if (d.group != last) {
                            last = d.group
                            Text(d.group.uppercase(), style = MaterialTheme.typography.labelSmall, color = Titan.Muted,
                                modifier = Modifier.padding(start = 12.dp, top = 14.dp, bottom = 4.dp))
                        }
                        NavigationDrawerItem(
                            label = { Text(d.title) }, icon = { Icon(d.icon, null) },
                            selected = route == d.route,
                            colors = NavigationDrawerItemDefaults.colors(selectedContainerColor = Titan.Surface2,
                                unselectedContainerColor = Titan.Surface),
                            onClick = {
                                scope.launch { drawer.close() }
                                nav.navigate(d.route) { launchSingleTop = true; popUpTo("dashboard") }
                            },
                        )
                    }
                    Spacer(Modifier.height(24.dp))
                }
            }
        },
    ) {
        Scaffold(
            containerColor = Titan.Bg,
            snackbarHost = { SnackbarHost(snackHost) },
            topBar = {
                TopAppBar(
                    title = {
                        Column {
                            Text(title, style = MaterialTheme.typography.titleMedium)
                            StatusDot(live, if (live) "LIVE" else "RECONNECTING")
                        }
                    },
                    navigationIcon = { IconButton({ scope.launch { drawer.open() } }) { Icon(Icons.Filled.Menu, "menu") } },
                    actions = {
                        IconButton({ nav.navigate("notifications") { launchSingleTop = true } }) {
                            BadgedBox(badge = { }) { Icon(Icons.Filled.Notifications, "notifications") }
                        }
                        IconButton({ nav.navigate("emergency") { launchSingleTop = true } }) {
                            Icon(Icons.Filled.Warning, "emergency", tint = Titan.Down)
                        }
                    },
                    colors = TopAppBarDefaults.topAppBarColors(containerColor = Titan.Bg),
                )
            },
        ) { pad ->
            val ctx = Ctx(app, nav, { m -> scope.launch { snackHost.showSnackbar(m) } }, tick)
            CompositionLocalProvider(LocalCtx provides ctx) {
                Box(Modifier.fillMaxSize().padding(pad)) { Routes(nav) }
            }
        }
    }
}

@Composable
private fun Routes(nav: NavHostController) {
    NavHost(nav, startDestination = "dashboard") {
        composable("dashboard") { DashboardScreen() }
        composable("forex") { MarketDashboardScreen("forex") }
        composable("binance") { MarketDashboardScreen("crypto") }
        composable("traders") { MarketplaceScreen() }
        composable("trader/{id}") { TraderDetailScreen(it.arguments?.getString("id")?.toIntOrNull() ?: 0) }
        composable("portfolio") { CopyPortfolioScreen() }
        composable("analytics") { AnalyticsScreen(null) }
        composable("analytics/{id}") { AnalyticsScreen(it.arguments?.getString("id")?.toIntOrNull()) }
        composable("positions") { PositionsScreen() }
        composable("history") { HistoryScreen() }
        composable("risk") { RiskScreen() }
        composable("ai") { AiCenterScreen() }
        composable("emergency") { EmergencyScreen() }
        composable("brokers") { BrokerConnectionsScreen() }
        composable("binance_api") { BinanceConnectionsScreen() }
        composable("notifications") { NotificationsScreen() }
        composable("settings") { SettingsScreen() }
        composable("audit") { AuditScreen() }
    }
}

val ContentPadding = PaddingValues(horizontal = 14.dp, vertical = 10.dp)
@Suppress("unused") private val unusedList = Icons.AutoMirrored.Filled.List

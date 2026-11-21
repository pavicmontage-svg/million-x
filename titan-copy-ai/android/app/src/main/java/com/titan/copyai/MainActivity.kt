package com.titan.copyai

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import com.titan.copyai.ui.nav.AppNav
import com.titan.copyai.ui.screens.PairingScreen
import com.titan.copyai.ui.theme.Titan
import com.titan.copyai.ui.theme.TitanTheme
import com.titan.copyai.ui.vm.AppVm

class MainActivity : ComponentActivity() {
    private val app: AppVm by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val start = intent?.getStringExtra("route")
        setContent {
            TitanTheme {
                val paired by app.paired.collectAsState()
                Box(Modifier.fillMaxSize().background(Titan.Bg).safeDrawingPadding()) {
                    if (paired) AppNav(app, start) else PairingScreen(app)
                }
            }
        }
    }
}

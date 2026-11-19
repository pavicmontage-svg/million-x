package com.millionx.ai

import android.Manifest
import android.os.Build
import android.os.Bundle
import android.os.SystemClock
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.biometric.BiometricManager
import androidx.biometric.BiometricManager.Authenticators.BIOMETRIC_STRONG
import androidx.biometric.BiometricManager.Authenticators.DEVICE_CREDENTIAL
import androidx.biometric.BiometricPrompt
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.core.content.ContextCompat
import androidx.fragment.app.FragmentActivity
import com.google.firebase.FirebaseApp
import com.google.firebase.messaging.FirebaseMessaging
import com.millionx.ai.data.jsonBody
import com.millionx.ai.ui.components.LocalAuth
import com.millionx.ai.ui.nav.AppNav
import com.millionx.ai.ui.screens.PairingScreen
import com.millionx.ai.ui.theme.MX
import com.millionx.ai.ui.theme.MillionXTheme
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

/**
 * Single activity. The app is locked behind biometrics (or device PIN) on
 * start and after 60 s in the background; sensitive actions (approvals,
 * settings, keys, prompts, limits) ask again.
 */
class MainActivity : FragmentActivity() {
    private var unlocked by mutableStateOf(false)
    private var paired by mutableStateOf(false)
    private var backgroundAt = 0L

    private val notifPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val app = MillionXApp.instance
        paired = app.store.token != null
        if (Build.VERSION.SDK_INT >= 33) notifPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        setContent {
            MillionXTheme {
                CompositionLocalProvider(LocalAuth provides { reason, action -> authenticate(reason, action) }) {
                    when {
                        !paired -> PairingScreen { paired = true; registerPush() }
                        !unlocked -> LockScreen()
                        else -> AppNav(onLogout = {
                            app.socket.stop(); app.store.clear(); paired = false
                        })
                    }
                }
            }
        }
        if (paired) {
            authenticate("Unlock MILLION-X") { unlocked = true }
            registerPush()
        } else unlocked = true
    }

    override fun onStop() {
        super.onStop()
        backgroundAt = SystemClock.elapsedRealtime()
    }

    override fun onStart() {
        super.onStart()
        if (paired && backgroundAt > 0 && SystemClock.elapsedRealtime() - backgroundAt > 60_000) {
            unlocked = false
            authenticate("Unlock MILLION-X") { unlocked = true }
        }
    }

    @androidx.compose.runtime.Composable
    private fun LockScreen() {
        Column(
            Modifier.fillMaxSize().background(MX.Bg),
            verticalArrangement = Arrangement.Center, horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text("MILLION-X AI", style = MaterialTheme.typography.headlineLarge, color = MX.Accent)
            Text("Locked", color = MX.Muted)
            Button(onClick = { authenticate("Unlock MILLION-X") { unlocked = true } }) { Text("Unlock") }
        }
    }

    fun authenticate(reason: String, onSuccess: () -> Unit) {
        val auths = BIOMETRIC_STRONG or DEVICE_CREDENTIAL
        if (BiometricManager.from(this).canAuthenticate(auths) != BiometricManager.BIOMETRIC_SUCCESS) {
            // No biometrics or screen lock configured: allow, but the server still enforces roles.
            onSuccess(); return
        }
        val prompt = BiometricPrompt(this, ContextCompat.getMainExecutor(this), object : BiometricPrompt.AuthenticationCallback() {
            override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) = onSuccess()
        })
        prompt.authenticate(
            BiometricPrompt.PromptInfo.Builder()
                .setTitle(reason)
                .setSubtitle("MILLION-X AI")
                .setAllowedAuthenticators(auths)
                .build()
        )
    }

    private fun registerPush() {
        if (FirebaseApp.getApps(this).isEmpty()) return  // google-services.json not bundled
        FirebaseMessaging.getInstance().token.addOnSuccessListener { token ->
            CoroutineScope(Dispatchers.IO).launch {
                runCatching { MillionXApp.instance.api.post("/api/devices/fcm", jsonBody("token" to token)) }
            }
        }
    }
}

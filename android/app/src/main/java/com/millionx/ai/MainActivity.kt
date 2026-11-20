package com.millionx.ai

import android.Manifest
import android.os.Build
import android.os.Bundle
import android.os.SystemClock
import android.widget.Toast
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
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.fragment.app.FragmentActivity
import androidx.lifecycle.lifecycleScope
import com.google.firebase.FirebaseApp
import com.google.firebase.messaging.FirebaseMessaging
import com.millionx.ai.data.Session
import com.millionx.ai.data.jsonBody
import com.millionx.ai.ui.components.LocalAuth
import com.millionx.ai.ui.nav.AppNav
import com.millionx.ai.ui.screens.PairingScreen
import com.millionx.ai.ui.theme.MX
import com.millionx.ai.ui.theme.MillionXTheme
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

/**
 * Single activity. Locked behind biometrics/device credential on start and after
 * 60 s in the background; sensitive actions re-authenticate. The emergency stop
 * is deliberately NOT behind biometrics so it can never be delayed.
 */
class MainActivity : FragmentActivity() {
    private var unlocked by mutableStateOf(false)
    private var paired by mutableStateOf(false)
    private var lockMessage by mutableStateOf<String?>(null)
    private var backgroundAt = 0L
    private var pendingAction: (() -> Unit)? = null
    private lateinit var prompt: BiometricPrompt

    private val notifPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val app = MillionXApp.instance
        paired = app.store.token != null
        if (com.millionx.ai.engine.LocalEngine.isEnabled(this)) {
            runCatching { com.millionx.ai.engine.LocalEngine.start(this) }  // keep the local engine alive
        }
        // BiometricPrompt must be created while the activity is being created
        prompt = BiometricPrompt(this, ContextCompat.getMainExecutor(this), object : BiometricPrompt.AuthenticationCallback() {
            override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) {
                val a = pendingAction
                pendingAction = null
                a?.invoke()
            }

            override fun onAuthenticationError(errorCode: Int, errString: CharSequence) {
                pendingAction = null
                if (errorCode != BiometricPrompt.ERROR_USER_CANCELED && errorCode != BiometricPrompt.ERROR_NEGATIVE_BUTTON &&
                    errorCode != BiometricPrompt.ERROR_CANCELED) {
                    Toast.makeText(this@MainActivity, errString, Toast.LENGTH_LONG).show()
                }
            }
        })
        if (Build.VERSION.SDK_INT >= 33) notifPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        lifecycleScope.launch {
            Session.expired.collect { reason ->
                app.socket.stop()
                app.store.token = null
                paired = false
                unlocked = true
                Toast.makeText(this@MainActivity, reason, Toast.LENGTH_LONG).show()
            }
        }
        setContent {
            MillionXTheme {
                CompositionLocalProvider(LocalAuth provides { reason, action -> authenticate(reason, sensitive = true, action) }) {
                    when {
                        !paired -> PairingScreen {
                            paired = true; unlocked = true; app.socket.start(); registerPush()
                        }
                        !unlocked -> LockScreen()
                        else -> AppNav(onLogout = {
                            app.socket.stop(); app.store.clear(); paired = false
                        })
                    }
                }
            }
        }
        if (paired) {
            authenticate("Unlock MILLION-X", sensitive = false) { unlocked = true }
            registerPush()
        } else unlocked = true
    }

    override fun onStop() {
        super.onStop()
        backgroundAt = SystemClock.elapsedRealtime()
        MillionXApp.instance.socket.stop()  // background events arrive by push; saves battery
    }

    override fun onStart() {
        super.onStart()
        if (paired) MillionXApp.instance.socket.start()
        if (paired && backgroundAt > 0 && SystemClock.elapsedRealtime() - backgroundAt > 60_000) {
            unlocked = false
            authenticate("Unlock MILLION-X", sensitive = false) { unlocked = true }
        }
    }

    @Composable
    private fun LockScreen() {
        Column(
            Modifier.fillMaxSize().background(MX.Bg).padding(24.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp, Alignment.CenterVertically),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text("MILLION-X AI", style = MaterialTheme.typography.headlineLarge, color = MX.Accent)
            Text("Locked", color = MX.Muted)
            lockMessage?.let { Text(it, color = MX.Warn, textAlign = TextAlign.Center) }
            Button(onClick = { authenticate("Unlock MILLION-X", sensitive = false) { unlocked = true } }) { Text("Unlock") }
        }
    }

    /**
     * sensitive = approvals, settings, keys, prompts, limits: refused when the phone has no
     * screen lock, because then nothing proves the owner is holding the phone.
     */
    fun authenticate(reason: String, sensitive: Boolean, onSuccess: () -> Unit) {
        val auths = BIOMETRIC_STRONG or DEVICE_CREDENTIAL
        when (BiometricManager.from(this).canAuthenticate(auths)) {
            BiometricManager.BIOMETRIC_SUCCESS -> {
                pendingAction = onSuccess
                prompt.authenticate(
                    BiometricPrompt.PromptInfo.Builder()
                        .setTitle(reason)
                        .setSubtitle("MILLION-X AI")
                        .setAllowedAuthenticators(auths)
                        .build()
                )
            }
            else -> {
                if (sensitive) {
                    Toast.makeText(this, "Set up a screen lock (fingerprint/face/PIN) to authorise this action",
                        Toast.LENGTH_LONG).show()
                } else {
                    lockMessage = "No screen lock is set up on this phone. Sensitive actions stay blocked until you add one."
                    onSuccess()
                }
            }
        }
    }

    private fun registerPush() {
        if (FirebaseApp.getApps(this).isEmpty()) return  // google-services.json not bundled
        FirebaseMessaging.getInstance().token.addOnSuccessListener { token ->
            lifecycleScope.launch(Dispatchers.IO) {
                runCatching { MillionXApp.instance.api.post("/api/devices/fcm", jsonBody("token" to token)) }
            }
        }
    }
}

package com.titan.copyai.data

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/**
 * Device-side secrets: only the backend URL and this device's bearer token.
 * Exchange / broker credentials are NEVER stored on the phone: they are sent once over TLS to the
 * backend, encrypted there with AES-256-GCM, and never returned.
 * Storage: EncryptedSharedPreferences, keys protected by the Android Keystore.
 */
class SecureStore(context: Context) {
    private val prefs: SharedPreferences = EncryptedSharedPreferences.create(
        context,
        "titan_secure",
        MasterKey.Builder(context).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build(),
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
    )

    var serverUrl: String
        get() = prefs.getString("server_url", "") ?: ""
        set(v) = prefs.edit().putString("server_url", v.trim().trimEnd('/')).apply()

    var token: String
        get() = prefs.getString("token", "") ?: ""
        set(v) = prefs.edit().putString("token", v).apply()

    var lastNotificationId: Long
        get() = prefs.getLong("last_notification_id", 0L)
        set(v) = prefs.edit().putLong("last_notification_id", v).apply()

    val isPaired: Boolean get() = serverUrl.isNotBlank() && token.isNotBlank()

    /** true = the engine runs on this phone (default); false = remote server mode. */
    var localMode: Boolean
        get() = prefs.getBoolean("local_mode", false)
        set(v) = prefs.edit().putBoolean("local_mode", v).apply()

    /** 32-byte AES-256-GCM key for the on-phone credential vault (Keystore-protected storage). */
    val engineMasterKey: String
        get() = prefs.getString("engine_master_key", null) ?: run {
            val bytes = ByteArray(32).also { java.security.SecureRandom().nextBytes(it) }
            val k = android.util.Base64.encodeToString(bytes, android.util.Base64.URL_SAFE or
                android.util.Base64.NO_PADDING or android.util.Base64.NO_WRAP)
            prefs.edit().putString("engine_master_key", k).commit()
            k
        }

    /** Optional Claude API key for the AI agents on the phone. */
    var anthropicKey: String
        get() = prefs.getString("anthropic_key", "") ?: ""
        set(v) = prefs.edit().putString("anthropic_key", v.trim()).apply()

    /** Unpair the UI. The engine's own master key is kept, otherwise stored credentials would be lost. */
    fun clear() {
        val key = prefs.getString("engine_master_key", null)
        prefs.edit().clear().apply()
        if (key != null) prefs.edit().putString("engine_master_key", key).commit()
    }
}

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

    fun clear() = prefs.edit().clear().apply()
}

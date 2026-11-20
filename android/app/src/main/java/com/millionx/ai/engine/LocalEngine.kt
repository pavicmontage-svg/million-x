package com.millionx.ai.engine

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.PowerManager
import android.provider.Settings
import androidx.core.content.ContextCompat
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import com.millionx.ai.MillionXApp
import com.millionx.ai.data.parseJson
import com.millionx.ai.data.str
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import okhttp3.Request

/**
 * The trading backend embedded in the app (Python via Chaquopy), served on
 * 127.0.0.1:8080 and kept alive by [EngineService].
 */
object LocalEngine {
    const val PORT = 8080
    const val URL = "http://127.0.0.1:$PORT"
    private const val PREFS = "mx_local_engine"

    fun isEnabled(ctx: Context) = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getBoolean("enabled", false)

    fun setEnabled(ctx: Context, on: Boolean) =
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putBoolean("enabled", on).apply()

    /** Starts the embedded server (idempotent). Called from the service thread. */
    fun startPython(ctx: Context) {
        if (!Python.isStarted()) Python.start(AndroidPlatform(ctx.applicationContext))
        val store = MillionXApp.instance.store
        val code = store.get("local_bootstrap") ?: run {
            val c = Python.getInstance().getModule("millionx.android_entry").callAttr("new_pairing_code").toString()
            store.put("local_bootstrap", c); c
        }
        Python.getInstance().getModule("millionx.android_entry")
            .callAttr("start_background", ctx.filesDir.absolutePath, code, PORT)
    }

    fun stopPython() {
        if (Python.isStarted()) Python.getInstance().getModule("millionx.android_entry").callAttr("stop")
    }

    fun pythonRunning(): Boolean =
        Python.isStarted() && Python.getInstance().getModule("millionx.android_entry").callAttr("is_running").toBoolean()

    fun start(ctx: Context) {
        setEnabled(ctx, true)
        ContextCompat.startForegroundService(ctx, Intent(ctx, EngineService::class.java))
    }

    fun stop(ctx: Context) {
        setEnabled(ctx, false)
        ctx.startService(Intent(ctx, EngineService::class.java).setAction(EngineService.ACTION_STOP))
    }

    /** Waits until the local server answers /api/health (first start unpacks Python: ~30-90 s). */
    suspend fun awaitServer(timeoutMs: Long = 180_000): Boolean = withContext(Dispatchers.IO) {
        val http = MillionXApp.instance.api.http
        val until = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < until) {
            try {
                http.newCall(Request.Builder().url("$URL/api/health").build()).execute().use { r ->
                    if (r.code == 200 || r.code == 503) return@withContext true
                }
            } catch (_: Exception) { }
            delay(1_000)
        }
        false
    }

    /** Starts the engine and pairs this phone as owner with the one-time local code. */
    suspend fun startAndPair(ctx: Context): String? {
        start(ctx)
        if (!awaitServer()) return "Local engine did not start in time"
        val app = MillionXApp.instance
        val code = app.store.get("local_bootstrap") ?: return "No local pairing code"
        return try {
            val r = app.api.pair(URL, code, "This phone (local engine)")
            if (r.str("token").isEmpty()) "Pairing failed" else null
        } catch (e: Exception) {
            e.message ?: "Pairing failed"
        }
    }

    fun ignoringBatteryOptimizations(ctx: Context): Boolean =
        ctx.getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(ctx.packageName)

    fun batteryOptimizationIntent(ctx: Context): Intent =
        Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:${ctx.packageName}"))

    /** Health of the LOCAL server (independent of which server the app is paired with). */
    suspend fun health(): kotlinx.serialization.json.JsonElement = withContext(Dispatchers.IO) {
        MillionXApp.instance.api.http.newCall(Request.Builder().url("$URL/api/health").build()).execute().use { r ->
            parseJson(r.body?.string().orEmpty())
        }
    }
}

package com.titan.copyai.engine

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.PowerManager
import android.provider.Settings
import androidx.core.content.ContextCompat
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import com.titan.copyai.TitanApp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import okhttp3.Request

/**
 * The TITAN engine embedded in the app (Python via Chaquopy): copy engine, risk engine, agents,
 * scheduler, SQLite database and an API bound to 127.0.0.1. Kept alive by [EngineService].
 */
object LocalEngine {
    private const val PREFS = "titan_engine"
    @Volatile var port: Int = 0
        private set
    @Volatile var lastProblem: String = ""

    private fun prefs(ctx: Context) = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
    fun isEnabled(ctx: Context) = prefs(ctx).getBoolean("enabled", false)
    private fun setEnabled(ctx: Context, on: Boolean) = prefs(ctx).edit().putBoolean("enabled", on).apply()

    private fun entry(ctx: Context) = run {
        if (!Python.isStarted()) Python.start(AndroidPlatform(ctx.applicationContext))
        Python.getInstance().getModule("titan.android_entry")
    }

    /** Starts the embedded engine (idempotent; blocking ~seconds on first start). Call off the main thread. */
    fun startPython(ctx: Context): Int {
        val store = (ctx.applicationContext as TitanApp).repo.store
        val p = entry(ctx).callAttr("start_background", ctx.filesDir.absolutePath, store.engineMasterKey,
            store.anthropicKey).toInt()
        port = p
        // the UI follows the engine to its (possibly new) port
        if (store.localMode) {
            val url = "http://127.0.0.1:$p"
            if (store.serverUrl != url) store.serverUrl = url
            if (store.token.isBlank()) store.token = entry(ctx).callAttr("local_token", "This phone").toString()
        }
        return p
    }

    fun pythonRunning(ctx: Context): Boolean = Python.isStarted() && entry(ctx).callAttr("is_running").toBoolean()
    fun pythonError(ctx: Context): String = if (Python.isStarted()) entry(ctx).callAttr("last_error").toString() else ""
    fun logTail(ctx: Context): String = runCatching { entry(ctx).callAttr("log_tail", ctx.filesDir.absolutePath, 200).toString() }
        .getOrElse { "Could not read engine log: ${it.message}" }

    fun stopPython(ctx: Context) {
        if (Python.isStarted()) runCatching { entry(ctx).callAttr("stop") }
    }

    fun start(ctx: Context) {
        setEnabled(ctx, true)
        ContextCompat.startForegroundService(ctx, Intent(ctx, EngineService::class.java))
    }

    fun stop(ctx: Context) {
        setEnabled(ctx, false)
        ctx.startService(Intent(ctx, EngineService::class.java).setAction(EngineService.ACTION_STOP))
    }

    /** Health of the local engine: (engine ok, short status text). */
    fun probe(ctx: Context): Pair<Boolean, String> {
        if (port <= 0) return false to "starting"
        return try {
            val http = (ctx.applicationContext as TitanApp).repo.client.http
            http.newCall(Request.Builder().url("http://127.0.0.1:$port/api/health").build()).execute().use { r ->
                val body = r.body?.string().orEmpty()
                val ok = body.contains("\"engine\":\"ok\"") || body.contains("\"engine\": \"ok\"")
                ok to if (ok) "running" else "degraded: ${body.take(120)}"
            }
        } catch (e: Exception) {
            false to "${e.javaClass.simpleName}: ${e.message ?: ""}".take(120)
        }
    }

    /** Switch the app to on-phone mode, start the engine and wait until it answers. */
    suspend fun startLocal(ctx: Context, onStatus: (String) -> Unit = {}, timeoutMs: Long = 240_000): String? {
        val store = (ctx.applicationContext as TitanApp).repo.store
        if (!store.localMode) {
            store.localMode = true
            store.token = ""   // a remote token is not valid for the local engine
        }
        start(ctx)
        val t0 = System.currentTimeMillis()
        return withContext(Dispatchers.IO) {
            while (System.currentTimeMillis() - t0 < timeoutMs) {
                val (ok, text) = probe(ctx)
                if (ok && store.isPaired) return@withContext null
                val err = runCatching { pythonError(ctx) }.getOrDefault("")
                if (err.isNotEmpty()) return@withContext "Engine error: $err"
                onStatus("Starting the engine on this phone… ${(System.currentTimeMillis() - t0) / 1000}s ($text)")
                delay(1000)
            }
            "The engine did not start within ${timeoutMs / 1000}s"
        }
    }

    fun ignoringBatteryOptimizations(ctx: Context): Boolean =
        ctx.getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(ctx.packageName)

    fun batteryOptimizationIntent(ctx: Context): Intent =
        Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:${ctx.packageName}"))
}

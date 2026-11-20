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
 * The trading backend embedded in the app (Python via Chaquopy), served on a
 * free 127.0.0.1 port (8080, or 18080/28080/38080 if another app uses 8080)
 * and kept alive by [EngineService].
 */
object LocalEngine {
    private const val PREFS = "mx_local_engine"
    @Volatile var port: Int = 0
        private set

    /** Base URL of the local server (port chosen by Python at start, persisted). */
    val URL: String get() = "http://127.0.0.1:${if (port > 0) port else savedPort()}"

    fun isLocalUrl(u: String?) = u != null && u.startsWith("http://127.0.0.1:")

    private fun prefs() = MillionXApp.instance.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
    private fun savedPort() = prefs().getInt("port", 8080)

    /** Last start/health problem, shown in the UI ("" = none). */
    @Volatile var lastProblem: String = ""

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
        val p = Python.getInstance().getModule("millionx.android_entry")
            .callAttr("start_background", ctx.filesDir.absolutePath, code, 0).toInt()
        port = p
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putInt("port", p).apply()
        // a previously paired local session must follow the server to its new port
        val store = MillionXApp.instance.store
        if (isLocalUrl(store.serverUrl) && store.serverUrl != URL) store.serverUrl = URL
    }

    private fun entry() = Python.getInstance().getModule("millionx.android_entry")

    fun pythonError(): String = if (Python.isStarted()) entry().callAttr("last_error").toString() else ""

    /** Tail of mx-data/server.log for the in-app log viewer. */
    fun logTail(ctx: Context, lines: Int = 120): String = try {
        if (!Python.isStarted()) Python.start(AndroidPlatform(ctx.applicationContext))
        entry().callAttr("log_tail", ctx.filesDir.absolutePath, lines).toString()
    } catch (e: Exception) {
        "Could not read engine log: ${e.message}"
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

    /** One probe of the local /api/health: our JSON (any HTTP code) or a readable problem. */
    data class Probe(val ours: Boolean, val code: Int, val json: kotlinx.serialization.json.JsonElement?, val problem: String)

    fun probe(): Probe = try {
        MillionXApp.instance.api.http.newCall(Request.Builder().url("$URL/api/health").build()).execute().use { r ->
            val body = r.body?.string().orEmpty()
            val j = runCatching { parseJson(body) }.getOrNull()
            val ours = j != null && (j.str("service") == "millionx" || j.str("mode").isNotEmpty())
            Probe(ours, r.code, if (ours) j else null,
                if (ours) "" else "HTTP ${r.code} from $URL: ${body.take(160).ifBlank { "(empty)" }}")
        }
    } catch (e: Exception) {
        Probe(false, 0, null, "${e.javaClass.simpleName}: ${e.message ?: ""}".take(160))
    }

    /** Waits until OUR server answers (first start unpacks Python: ~30-90 s). */
    suspend fun awaitServer(timeoutMs: Long = 240_000, onStatus: (String) -> Unit = {}): Boolean = withContext(Dispatchers.IO) {
        val until = System.currentTimeMillis() + timeoutMs
        val t0 = System.currentTimeMillis()
        while (System.currentTimeMillis() < until) {
            if (port > 0) {
                val p = probe()
                if (p.ours) { lastProblem = ""; return@withContext true }
                lastProblem = p.problem
            }
            val err = runCatching { pythonError() }.getOrDefault("")
            if (err.isNotEmpty()) { lastProblem = "Engine error: $err"; return@withContext false }
            onStatus("Starting the engine… ${(System.currentTimeMillis() - t0) / 1000}s" +
                if (port > 0) " (port $port)" else " (unpacking Python)")
            delay(1_000)
        }
        if (lastProblem.isEmpty()) lastProblem = "Local engine did not answer within ${timeoutMs / 1000}s"
        false
    }

    /** Starts the engine and pairs this phone as owner with a fresh in-process code. */
    suspend fun startAndPair(ctx: Context, onStatus: (String) -> Unit = {}): String? {
        start(ctx)
        if (!awaitServer(onStatus = onStatus)) return lastProblem.ifEmpty { "Local engine did not start" }
        onStatus("Engine is up — pairing this phone…")
        val app = MillionXApp.instance
        return try {
            val code = withContext(Dispatchers.IO) {
                entry().callAttr("local_pairing_code", ctx.filesDir.absolutePath).toString()
            }
            val r = app.api.pair(URL, code, "This phone (local engine)")
            if (r.str("token").isEmpty()) "Pairing failed (no token)" else null
        } catch (e: Exception) {
            "Pairing failed: ${e.message ?: e.javaClass.simpleName}"
        }
    }

    fun ignoringBatteryOptimizations(ctx: Context): Boolean =
        ctx.getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(ctx.packageName)

    fun batteryOptimizationIntent(ctx: Context): Intent =
        Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:${ctx.packageName}"))

    /** Health of the LOCAL server (independent of which server the app is paired with). */
    suspend fun health(): Probe = withContext(Dispatchers.IO) { probe() }
}

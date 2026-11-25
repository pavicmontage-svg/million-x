package ai.quanttitan.app

import android.content.Context
import android.util.Base64
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.security.SecureRandom

/**
 * The complete Python platform (engines, risk manager, execution, research lab, API, dashboard)
 * runs inside the app via Chaquopy. The server binds to 127.0.0.1 only; the WebView and the
 * service authenticate with a random device token kept in app-private storage.
 */
object Backend {
    const val DEFAULT_PORT = 8765

    @Volatile var port: Int = 0
    @Volatile var lastError: String = ""

    fun token(ctx: Context): String {
        val prefs = ctx.getSharedPreferences("qt", Context.MODE_PRIVATE)
        prefs.getString("device_token", null)?.let { return it }
        val bytes = ByteArray(32).also { SecureRandom().nextBytes(it) }
        val t = Base64.encodeToString(bytes, Base64.URL_SAFE or Base64.NO_WRAP or Base64.NO_PADDING)
        prefs.edit().putString("device_token", t).apply()
        return t
    }

    @Synchronized
    fun start(ctx: Context): Int {
        if (port != 0 && running()) return port
        if (!Python.isStarted()) Python.start(AndroidPlatform(ctx.applicationContext))
        val dir = File(ctx.filesDir, "qt").apply { mkdirs() }
        val mod = Python.getInstance().getModule("quanttitan.android_entry")
        port = mod.callAttr("start", dir.absolutePath, DEFAULT_PORT, token(ctx)).toInt()
        return port
    }

    fun running(): Boolean = try {
        Python.isStarted() && Python.getInstance().getModule("quanttitan.android_entry").callAttr("status").toString() == "running"
    } catch (e: Exception) { false }

    fun pythonStatus(): String = try {
        if (!Python.isStarted()) "not started"
        else Python.getInstance().getModule("quanttitan.android_entry").callAttr("status").toString()
    } catch (e: Exception) { "status unavailable: ${e.message}" }

    fun stop() {
        try {
            if (Python.isStarted()) Python.getInstance().getModule("quanttitan.android_entry").callAttr("stop")
        } catch (_: Exception) { }
    }

    private fun get(ctx: Context, path: String): String? {
        if (port == 0) return null
        return try {
            val c = URL("http://127.0.0.1:$port$path").openConnection() as HttpURLConnection
            c.connectTimeout = 3000; c.readTimeout = 8000
            c.setRequestProperty("Authorization", "Bearer ${token(ctx)}")
            c.inputStream.bufferedReader().use { it.readText() }.also { c.disconnect() }
        } catch (e: Exception) { null }
    }

    fun health(ctx: Context): JSONObject? = get(ctx, "/api/health")?.let { runCatching { JSONObject(it) }.getOrNull() }
    fun status(ctx: Context): JSONObject? = get(ctx, "/api/status")?.let { runCatching { JSONObject(it) }.getOrNull() }
    fun alerts(ctx: Context, after: Long): JSONArray? = get(ctx, "/api/alerts?after=$after&limit=50")?.let { runCatching { JSONArray(it) }.getOrNull() }
}

package com.millionx.ai

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import com.millionx.ai.data.arr
import com.millionx.ai.data.parseJson
import com.millionx.ai.data.str
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.util.concurrent.TimeUnit

/** Proves the full Python backend runs inside the APK on Android and answers the app's API. */
@RunWith(AndroidJUnit4::class)
class EmbeddedBackendTest {
    private val http = OkHttpClient.Builder().readTimeout(30, TimeUnit.SECONDS).build()
    private val base = "http://127.0.0.1:8095"

    private fun get(path: String, token: String? = null): Pair<Int, String> {
        val b = Request.Builder().url(base + path)
        if (token != null) b.header("Authorization", "Bearer $token")
        http.newCall(b.build()).execute().use { return it.code to (it.body?.string() ?: "") }
    }

    @Test(timeout = 300_000)
    fun embeddedServerStartsPairsAndServesTenBots() {
        val ctx = InstrumentationRegistry.getInstrumentation().targetContext
        if (!Python.isStarted()) Python.start(AndroidPlatform(ctx))
        val entry = Python.getInstance().getModule("millionx.android_entry")
        val dir = File(ctx.cacheDir, "mx-embedded-test").apply { mkdirs() }
        val code = entry.callAttr("new_pairing_code").toString()
        entry.callAttr("start_background", dir.absolutePath, code, 8095)

        var health: Pair<Int, String>? = null
        val until = System.currentTimeMillis() + 240_000
        while (System.currentTimeMillis() < until) {
            health = runCatching { get("/api/health") }.getOrNull()
            if (health != null && (health.first == 200 || health.first == 503)) break
            Thread.sleep(1_000)
        }
        assertTrue("server did not answer: ${entry.callAttr("last_error")}", health != null && health.first in setOf(200, 503))
        assertEquals("paper", parseJson(health!!.second).str("mode"))

        val body = """{"code":"$code","device_name":"instrumented-test"}""".toRequestBody("application/json".toMediaType())
        val token = http.newCall(Request.Builder().url("$base/api/pair").post(body).build()).execute().use {
            parseJson(it.body!!.string()).str("token")
        }
        assertTrue(token.isNotEmpty())
        val (c, bots) = get("/api/bots", token)
        assertEquals(200, c)
        assertEquals(10, parseJson(bots).arr.size)
        entry.callAttr("stop")
    }
}

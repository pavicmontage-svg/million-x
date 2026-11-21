package com.titan.copyai

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.titan.copyai.engine.LocalEngine
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

/** Starts the real embedded engine (Python, SQLite, copy/risk engine, API on 127.0.0.1) on the device. */
@RunWith(AndroidJUnit4::class)
class EngineOnDeviceTest {
    @Test fun engineRunsOnThePhone() = runBlocking {
        val ctx = InstrumentationRegistry.getInstrumentation().targetContext
        val err = LocalEngine.startLocal(ctx, timeoutMs = 300_000)
        assertNull("engine failed: $err\n${LocalEngine.logTail(ctx)}", err)
        val (ok, status) = LocalEngine.probe(ctx)
        assertTrue("health: $status", ok)
        val repo = (ctx.applicationContext as TitanApp).repo
        val d = try { repo.api.dashboard() } catch (e: Exception) {
            throw AssertionError("dashboard failed: ${e.message}\n--- engine log ---\n${LocalEngine.logTail(ctx)}", e)
        }
        assertEquals(listOf("Forex Paper", "Crypto Paper"), d.portfolios.map { it.name })
        assertTrue(d.copyEngine.toString(), d.copyEngine["running"].toString() == "true")
        assertEquals(7, repo.api.traders().count { it.platform in setOf("zulutrade", "ctrader_copy") })
    }
}

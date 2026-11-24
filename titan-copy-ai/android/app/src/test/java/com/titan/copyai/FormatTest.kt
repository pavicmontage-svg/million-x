package com.titan.copyai

import com.titan.copyai.data.api.Dashboard
import com.titan.copyai.data.api.TitanJson
import com.titan.copyai.data.api.TraderDetail
import com.titan.copyai.ui.components.Fmt
import com.titan.copyai.ui.components.UNKNOWN
import com.titan.copyai.ui.components.metricText
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class FormatTest {
    @Test fun missingValuesAreUnknown() {
        assertEquals(UNKNOWN, Fmt.num(null))
        assertEquals(UNKNOWN, Fmt.pct(null))
        assertEquals(UNKNOWN, metricText("roi_30d", JsonNull))
        assertEquals(UNKNOWN, metricText("profit_factor", null))
    }

    @Test fun formatsMetrics() {
        assertEquals("12.50%", metricText("roi_30d", JsonPrimitive(12.5)))
        assertEquals("1.850", metricText("profit_factor", JsonPrimitive(1.85)))
        assertEquals("42", metricText("total_trades", JsonPrimitive(42)))
        assertEquals("+1,234.50", Fmt.signed(1234.5))
    }

    @Test fun parsesBackendTimestamps() {
        val ms = Fmt.epochMillis("2026-11-21T19:54:44.680842+00:00")
        assertEquals(1795290884680L, ms)
        // SQLite on the phone (older rows) returns no offset: it is UTC
        assertEquals(1795290884680L, Fmt.epochMillis("2026-11-21T19:54:44.680842"))
    }

    @Test fun decodesDashboardWithUnknowns() {
        val json = """{"portfolios":[{"id":1,"name":"Forex Paper","market":"forex","venue":"paper_forex","mode":"paper",
            "simulated":true,"currency":"EUR","status":"active","equity":null,"active_traders":0,"open_positions":0,
            "initial_capital":500,"extra_field":1}],"copy_engine":{"running":false},"kill_switch":{},
            "market_status":{"minutes_to_rollover":42.0},"unread_notifications":3}"""
        val d = TitanJson.decodeFromString(Dashboard.serializer(), json)
        assertEquals(1, d.portfolios.size)
        assertNull(d.portfolios[0].equity)
        assertEquals(500.0, d.portfolios[0].initialCapital, 0.0)
    }

    @Test fun decodesTraderDetailMetricsWithNulls() {
        val json = """{"id":3,"name":"Money Pile","platform":"zulutrade","market":"forex","status":"candidate",
            "copy_capability":"monitor_only","data_status":"unverified","metrics":{"roi_30d":null,"win_rate":null}}"""
        val t = TitanJson.decodeFromString(TraderDetail.serializer(), json)
        assertEquals(UNKNOWN, metricText("roi_30d", t.metrics["roi_30d"]))
    }
}

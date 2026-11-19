package com.millionx.ai

import com.millionx.ai.data.bool
import com.millionx.ai.data.fmt
import com.millionx.ai.data.get
import com.millionx.ai.data.jsonBody
import com.millionx.ai.data.list
import com.millionx.ai.data.num
import com.millionx.ai.data.numOrNull
import com.millionx.ai.data.parseJson
import com.millionx.ai.data.pct
import com.millionx.ai.data.str
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class JsonHelpersTest {
    private val j = parseJson("""{"a":1.5,"b":"x","c":null,"d":true,"e":0,"f":[1,2],"g":{"h":"inf"},"i":"-inf"}""")

    @Test fun readsValuesLeniently() {
        assertEquals(1.5, j.num("a"), 1e-12)
        assertEquals("x", j.str("b"))
        assertEquals("", j.str("c"))
        assertNull(j.numOrNull("c"))
        assertTrue(j.bool("d"))
        assertFalse(j.bool("e"))
        assertEquals(2, j.list("f").size)
        assertEquals(Double.POSITIVE_INFINITY, j["g"].numOrNull("h")!!, 0.0)
        assertEquals(Double.NEGATIVE_INFINITY, j.numOrNull("i")!!, 0.0)
        assertEquals(0.0, j["missing"].num("x"), 0.0)
    }

    @Test fun formatsMissingValuesWithoutCrashing() {
        assertEquals("—", (null as Double?).fmt())
        assertEquals("—", Double.NaN.fmt())
        assertEquals("∞", Double.POSITIVE_INFINITY.fmt())
        assertEquals("—", (null as Double?).pct())
    }

    @Test fun buildsBodies() {
        val b = parseJson(jsonBody("a" to 1, "b" to true, "c" to null, "d" to "s"))
        assertEquals(1.0, b.num("a"), 0.0)
        assertTrue(b.bool("b"))
        assertEquals("s", b.str("d"))
    }
}

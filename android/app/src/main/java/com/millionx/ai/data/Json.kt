package com.millionx.ai.data

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.longOrNull

/** Lenient helpers for reading backend JSON without generated serializers. */
val JsonFmt = Json { ignoreUnknownKeys = true; isLenient = true }

fun parseJson(text: String): JsonElement = JsonFmt.parseToJsonElement(text)

val JsonElement?.obj: JsonObject? get() = this as? JsonObject
val JsonElement?.arr: List<JsonElement> get() = (this as? JsonArray)?.toList() ?: emptyList()

operator fun JsonElement?.get(key: String): JsonElement? = (this as? JsonObject)?.get(key)?.takeIf { it !is JsonNull }

fun JsonElement?.str(key: String, def: String = ""): String =
    (this[key] as? JsonPrimitive)?.let { if (it.isString) it.content else it.toString() } ?: def

fun JsonElement?.numOrNull(key: String): Double? = (this[key] as? JsonPrimitive)?.let {
    it.doubleOrNull ?: when (it.content) { "inf" -> Double.POSITIVE_INFINITY; "-inf" -> Double.NEGATIVE_INFINITY; else -> null }
}

fun JsonElement?.num(key: String, def: Double = 0.0): Double = numOrNull(key) ?: def
fun JsonElement?.long(key: String, def: Long = 0): Long = (this[key] as? JsonPrimitive)?.longOrNull ?: def
fun JsonElement?.bool(key: String, def: Boolean = false): Boolean =
    (this[key] as? JsonPrimitive)?.let { it.booleanOrNull ?: (it.longOrNull?.let { n -> n != 0L }) } ?: def

fun JsonElement?.list(key: String): List<JsonElement> = this[key].arr

fun JsonElement?.keys(): Set<String> = (this as? JsonObject)?.keys ?: emptySet()

fun Double?.fmt(d: Int = 2): String = when {
    this == null || this.isNaN() -> "—"
    this.isInfinite() -> if (this > 0) "∞" else "-∞"
    else -> "%.${d}f".format(this)
}

fun Double?.pct(d: Int = 1): String = if (this == null || this.isNaN()) "—" else "%.${d}f%%".format(this * 100)

fun jsonBody(vararg pairs: Pair<String, Any?>): String {
    val m = pairs.associate { (k, v) ->
        k to when (v) {
            null -> JsonNull
            is Number -> JsonPrimitive(v)
            is Boolean -> JsonPrimitive(v)
            is JsonElement -> v
            is List<*> -> JsonArray(v.map { JsonPrimitive(it.toString()) })
            else -> JsonPrimitive(v.toString())
        }
    }
    return JsonObject(m).toString()
}

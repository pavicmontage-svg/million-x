package com.titan.copyai.ui.components

import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.doubleOrNull
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale
import kotlin.math.abs

/** Missing data is always shown as UNKNOWN - never estimated. */
const val UNKNOWN = "UNKNOWN"

object Fmt {
    fun num(v: Double?, digits: Int = 2): String = v?.let { String.format(Locale.US, "%,.${digits}f", it) } ?: UNKNOWN
    fun signed(v: Double?, digits: Int = 2): String = v?.let { String.format(Locale.US, "%+,.${digits}f", it) } ?: UNKNOWN
    fun pct(v: Double?, digits: Int = 2): String = v?.let { String.format(Locale.US, "%.${digits}f%%", it) } ?: UNKNOWN
    fun signedPct(v: Double?, digits: Int = 2): String = v?.let { String.format(Locale.US, "%+.${digits}f%%", it) } ?: UNKNOWN
    fun money(v: Double?, ccy: String, signed: Boolean = false): String =
        v?.let { (if (signed) signed(it) else num(it)) + " " + ccy } ?: UNKNOWN
    fun price(v: Double?): String = when {
        v == null -> UNKNOWN
        abs(v) >= 1000 -> String.format(Locale.US, "%,.2f", v)
        abs(v) >= 10 -> String.format(Locale.US, "%.3f", v)
        abs(v) >= 1 -> String.format(Locale.US, "%.5f", v)
        else -> String.format(Locale.US, "%.6f", v)
    }

    private val timeFmt = DateTimeFormatter.ofPattern("dd MMM HH:mm", Locale.US).withZone(ZoneId.systemDefault())
    fun time(iso: String?): String = iso?.let {
        runCatching { timeFmt.format(Instant.parse(normalizeIso(it))) }.getOrDefault(it)
    } ?: UNKNOWN

    fun normalizeIso(s: String): String {
        // backend emits e.g. 2026-11-21T19:54:44.680842+00:00 -> Instant.parse needs Z or offset handling
        return runCatching { java.time.OffsetDateTime.parse(s).toInstant().toString() }
            .recoverCatching { java.time.LocalDateTime.parse(s).toInstant(java.time.ZoneOffset.UTC).toString() } // no offset = UTC
            .getOrDefault(s)
    }

    fun epochMillis(iso: String): Long = runCatching { Instant.parse(normalizeIso(iso)).toEpochMilli() }.getOrDefault(0L)

    fun label(key: String): String = key.replace('_', ' ').replace("pct", "%").replace("roi", "ROI")
        .replaceFirstChar { it.uppercase() }
}

fun JsonElement?.d(): Double? = (this as? JsonPrimitive)?.takeIf { it !is JsonNull }?.doubleOrNull
fun JsonElement?.s(): String? = (this as? JsonPrimitive)?.takeIf { it !is JsonNull }?.content
fun JsonElement?.b(): Boolean? = (this as? JsonPrimitive)?.takeIf { it !is JsonNull }?.booleanOrNull
fun JsonElement?.obj(): JsonObject? = this as? JsonObject
fun JsonObject?.at(vararg path: String): JsonElement? {
    var cur: JsonElement? = this
    for (p in path) cur = (cur as? JsonObject)?.get(p)
    return cur
}

/** Human-readable rendering of a metric value (null -> UNKNOWN). */
fun metricText(key: String, v: JsonElement?): String {
    val d = v.d()
    return when {
        v == null || v is JsonNull -> UNKNOWN
        d == null -> v.s() ?: v.toString()
        key.startsWith("roi") || key.endsWith("_pct") || key == "win_rate" || key == "monthly_consistency" ||
            key == "sl_usage_rate" || key == "return_volatility_ann" -> Fmt.pct(d)
        key in setOf("total_trades", "max_consecutive_losses", "current_positions", "active_copiers", "account_age_days") ->
            d.toLong().toString()
        else -> Fmt.num(d, if (abs(d) < 10) 3 else 2)
    }
}

package com.millionx.ai.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonElement
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.util.concurrent.TimeUnit

class ApiException(val code: Int, message: String) : IOException(message)

/** REST client for the MILLION-X backend. All calls run on Dispatchers.IO. */
class Api(private val store: SecureStore) {
    val http: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .pingInterval(20, TimeUnit.SECONDS)
        .build()
    private val jsonType = "application/json".toMediaType()

    val base: String get() = store.serverUrl ?: ""

    suspend fun get(path: String): JsonElement = call("GET", path, null)
    suspend fun post(path: String, body: String = "{}"): JsonElement = call("POST", path, body)
    suspend fun put(path: String, body: String): JsonElement = call("PUT", path, body)

    suspend fun call(method: String, path: String, body: String?): JsonElement = withContext(Dispatchers.IO) {
        if (base.isEmpty()) throw ApiException(0, "Server not configured")
        val b = Request.Builder().url(base + path)
        store.token?.let { b.header("Authorization", "Bearer $it") }
        when (method) {
            "GET" -> b.get()
            else -> b.method(method, (body ?: "{}").toRequestBody(jsonType))
        }
        http.newCall(b.build()).execute().use { r ->
            val text = r.body?.string().orEmpty()
            val parsed = runCatching { parseJson(text) }.getOrNull()
            if (!r.isSuccessful) {
                throw ApiException(r.code, parsed.str("error").ifEmpty { "HTTP ${r.code}" })
            }
            parsed ?: throw ApiException(r.code, "Invalid response")
        }
    }

    /** Pairing with a one-time code printed by `python -m millionx.cli pair`. */
    suspend fun pair(server: String, code: String, deviceName: String): JsonElement {
        store.serverUrl = server
        store.token = null
        val r = post("/api/pair", jsonBody("code" to code, "device_name" to deviceName))
        store.token = r.str("token")
        store.role = r.str("role")
        return r
    }
}

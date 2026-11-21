package com.titan.copyai.data.api

import com.titan.copyai.data.SecureStore
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import retrofit2.HttpException
import retrofit2.Retrofit
import retrofit2.converter.kotlinx.serialization.asConverterFactory
import java.util.concurrent.TimeUnit

val TitanJson = Json { ignoreUnknownKeys = true; explicitNulls = false; coerceInputValues = true; isLenient = true }

class ApiClient(private val store: SecureStore) {
    private var cachedBase: String? = null
    private var cachedApi: ApiService? = null

    val http: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .pingInterval(25, TimeUnit.SECONDS)
        .addInterceptor { chain ->
            val b = chain.request().newBuilder()
            val t = store.token
            if (t.isNotBlank()) b.header("Authorization", "Bearer $t")
            chain.proceed(b.build())
        }
        .build()

    fun api(baseOverride: String? = null): ApiService {
        val base = (baseOverride ?: store.serverUrl).trimEnd('/') + "/"
        if (base != cachedBase || cachedApi == null) {
            require(base.startsWith("https://") || base.startsWith("http://10.0.2.2") ||
                base.startsWith("http://localhost") || base.startsWith("http://127.0.0.1")) {
                "The server URL must use https://"
            }
            cachedApi = Retrofit.Builder().baseUrl(base).client(http)
                .addConverterFactory(TitanJson.asConverterFactory("application/json".toMediaType()))
                .build().create(ApiService::class.java)
            cachedBase = base
        }
        return cachedApi!!
    }

    /** Live event stream (notifications, positions, signals, portfolio updates). */
    fun openSocket(onEvent: (JsonObject) -> Unit, onState: (Boolean) -> Unit): WebSocket? {
        if (!store.isPaired) return null
        val wsUrl = store.serverUrl.replaceFirst("https://", "wss://").replaceFirst("http://", "ws://") +
            "/api/ws?token=" + store.token
        return http.newWebSocket(Request.Builder().url(wsUrl).build(), object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) = onState(true)
            override fun onMessage(webSocket: WebSocket, text: String) {
                runCatching { TitanJson.parseToJsonElement(text) as JsonObject }.getOrNull()?.let {
                    if (it["event"]?.jsonPrimitive?.content != "ping") onEvent(it)
                }
            }
            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = onState(false)
            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) = onState(false)
        })
    }
}

fun Throwable.userMessage(): String = when (this) {
    is HttpException -> {
        val body = runCatching { response()?.errorBody()?.string() }.getOrNull().orEmpty()
        val detail = runCatching {
            (TitanJson.parseToJsonElement(body) as JsonObject)["detail"]?.toString()?.trim('"')
        }.getOrNull()
        "HTTP ${code()}: ${detail ?: message()}"
    }
    is java.net.UnknownHostException -> "Server not reachable (DNS)"
    is java.net.ConnectException -> "Server not reachable"
    is java.net.SocketTimeoutException -> "Server timeout"
    else -> message ?: javaClass.simpleName
}

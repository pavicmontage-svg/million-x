package com.millionx.ai.data

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonElement
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener

data class LiveEvent(val type: String, val ts: Long, val data: JsonElement?)

/** WebSocket stream of live trading events with automatic reconnect. */
class LiveSocket(private val api: Api, private val store: SecureStore) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val _events = MutableSharedFlow<LiveEvent>(extraBufferCapacity = 256)
    val events: SharedFlow<LiveEvent> = _events
    private val _connected = MutableStateFlow(false)
    val connected: StateFlow<Boolean> = _connected
    private var ws: WebSocket? = null
    private var wanted = false
    private var backoff = 1_000L

    fun start() {
        if (wanted) return
        wanted = true
        connect()
    }

    fun stop() {
        wanted = false
        ws?.close(1000, "bye")
        ws = null
        _connected.value = false
    }

    private fun connect() {
        val base = store.serverUrl ?: return
        if (store.token == null) return
        ws?.cancel()
        val url = base.replaceFirst("https://", "wss://").replaceFirst("http://", "ws://") + "/ws"
        ws = api.http.newWebSocket(Request.Builder().url(url).build(), object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                webSocket.send(jsonBody("token" to (store.token ?: "")))
                _connected.value = true
                backoff = 1_000L
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                val j = runCatching { parseJson(text) }.getOrNull() ?: return
                if (j.str("type") == "error" && j.str("data") == "unauthorized") {
                    wanted = false
                    Session.expire("Live connection rejected: pair again")
                    return
                }
                _events.tryEmit(LiveEvent(j.str("type"), j.long("ts"), j["data"]))
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                if (code == 4401) { wanted = false; Session.expire("Live connection rejected: pair again") }
                retry()
            }
            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) = retry()
        })
    }

    private fun retry() {
        _connected.value = false
        if (!wanted) return
        scope.launch {
            delay(backoff)
            backoff = (backoff * 2).coerceAtMost(30_000L)
            if (wanted) connect()
        }
    }
}

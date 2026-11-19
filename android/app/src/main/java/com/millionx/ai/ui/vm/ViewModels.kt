package com.millionx.ai.ui.vm

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.millionx.ai.MillionXApp
import com.millionx.ai.data.LiveEvent
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonElement

sealed class Load<out T> {
    data object Loading : Load<Nothing>()
    data class Error(val message: String) : Load<Nothing>()
    data class Ok<T>(val data: T) : Load<T>()
}

/**
 * Generic MVVM resource: loads one backend endpoint, refreshes on a timer and
 * whenever a matching live event arrives over the WebSocket.
 */
class ResourceVM(
    private val path: String,
    private val refreshMs: Long = 15_000,
    private val liveTypes: Set<String> = emptySet(),
) : ViewModel() {
    private val app = MillionXApp.instance
    private val _state = MutableStateFlow<Load<JsonElement>>(Load.Loading)
    val state: StateFlow<Load<JsonElement>> = _state
    private val _messages = MutableSharedFlow<String>(extraBufferCapacity = 8)
    val messages: SharedFlow<String> = _messages
    private var loader: Job? = null

    init {
        refresh()
        viewModelScope.launch {
            while (isActive) {
                delay(refreshMs); refresh(silent = true)
            }
        }
        if (liveTypes.isNotEmpty()) viewModelScope.launch {
            app.socket.events.collect { if (it.type in liveTypes) refresh(silent = true) }
        }
    }

    fun refresh(silent: Boolean = false) {
        if (loader?.isActive == true) return
        loader = viewModelScope.launch {
            if (!silent && _state.value !is Load.Ok) _state.value = Load.Loading
            _state.value = try {
                Load.Ok(app.api.get(path))
            } catch (e: Exception) {
                if (silent && _state.value is Load.Ok) _state.value else Load.Error(e.message ?: "error")
            }
        }
    }

    fun action(method: String, path: String, body: String = "{}", success: String = "Done",
               onOk: (JsonElement) -> Unit = {}) {
        viewModelScope.launch {
            try {
                val r = app.api.call(method, path, body)
                _messages.tryEmit(success)
                onOk(r)
                refresh(silent = true)
            } catch (e: Exception) {
                _messages.tryEmit(e.message ?: "Request failed")
            }
        }
    }
}

/** Live event feed for the whole app (alerts, trades, signals). */
class LiveVM : ViewModel() {
    private val app = MillionXApp.instance
    val connected = app.socket.connected
    private val _feed = MutableStateFlow<List<LiveEvent>>(emptyList())
    val feed: StateFlow<List<LiveEvent>> = _feed
    private val _alerts = MutableSharedFlow<LiveEvent>(extraBufferCapacity = 16)
    val alerts: SharedFlow<LiveEvent> = _alerts

    init {
        app.socket.start()
        viewModelScope.launch {
            app.socket.events.collect { e ->
                if (e.type != "ping" && e.type != "heartbeat") _feed.value = (listOf(e) + _feed.value).take(200)
                if (e.type in setOf("alert", "approval_request", "trade", "position_opened")) _alerts.tryEmit(e)
            }
        }
    }
}

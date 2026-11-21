package com.titan.copyai.ui.vm

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.titan.copyai.TitanApp
import com.titan.copyai.data.Cached
import com.titan.copyai.data.Repository
import com.titan.copyai.data.api.userMessage
import com.titan.copyai.work.Notifier
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.WebSocket

sealed interface UiState<out T> {
    data object Loading : UiState<Nothing>
    data class Data<T>(val value: T, val stale: Boolean = false, val updatedAt: Long = 0, val error: String? = null) : UiState<T>
    data class Error(val message: String) : UiState<Nothing>
}

/** Generic screen ViewModel: load, refresh, run actions, surface messages; auto-refreshes on live events. */
class LoadVm<T>(private val loader: suspend () -> Cached<T>) : ViewModel() {
    private val _state = MutableStateFlow<UiState<T>>(UiState.Loading)
    val state: StateFlow<UiState<T>> = _state.asStateFlow()
    private val _msg = MutableSharedFlow<String>(extraBufferCapacity = 4)
    val messages: SharedFlow<String> = _msg.asSharedFlow()
    private var job: Job? = null

    init { refresh() }

    fun refresh(silent: Boolean = false) {
        job?.cancel()
        job = viewModelScope.launch {
            if (!silent && _state.value !is UiState.Data) _state.value = UiState.Loading
            _state.value = try {
                val c = loader()
                UiState.Data(c.value, c.stale, c.updatedAt, c.error)
            } catch (e: Exception) {
                if (silent && _state.value is UiState.Data) _state.value else UiState.Error(e.userMessage())
            }
        }
    }

    fun action(success: String? = null, refreshAfter: Boolean = true, block: suspend () -> Unit) {
        viewModelScope.launch {
            try {
                block()
                success?.let { _msg.emit(it) }
                if (refreshAfter) refresh(silent = true)
            } catch (e: Exception) {
                _msg.emit(e.userMessage())
            }
        }
    }
}

fun <T> live(v: T) = Cached(v, System.currentTimeMillis(), false)

/** Activity-scoped state: pairing, live socket, global refresh ticks. */
class AppVm(app: Application) : AndroidViewModel(app) {
    val repo: Repository = (app as TitanApp).repo
    private val _paired = MutableStateFlow(repo.store.isPaired)
    val paired: StateFlow<Boolean> = _paired.asStateFlow()
    private val _live = MutableStateFlow(false)
    val liveConnected: StateFlow<Boolean> = _live.asStateFlow()
    private val _tick = MutableStateFlow(0L)
    val refreshTick: StateFlow<Long> = _tick.asStateFlow()
    private val _pairError = MutableStateFlow<String?>(null)
    val pairError: StateFlow<String?> = _pairError.asStateFlow()
    private var socket: WebSocket? = null

    init { if (repo.store.isPaired) connectLive() }

    fun pair(url: String, code: String) = viewModelScope.launch {
        _pairError.value = null
        try {
            repo.pair(url, code, android.os.Build.MODEL ?: "android")
            _paired.value = true
            connectLive()
        } catch (e: Exception) {
            _pairError.value = e.userMessage()
        }
    }

    fun logout() = viewModelScope.launch {
        socket?.close(1000, "logout")
        repo.logout()
        _paired.value = false
    }

    private fun connectLive() {
        viewModelScope.launch {
            while (_paired.value) {
                if (!_live.value) {
                    socket?.cancel()
                    socket = repo.client.openSocket(onEvent = ::onEvent, onState = { _live.value = it })
                }
                runCatching { Notifier.show(getApplication(), repo.syncNotifications()) }
                delay(if (_live.value) 60_000 else 10_000)
            }
        }
    }

    private fun onEvent(ev: JsonObject) {
        _tick.value = System.currentTimeMillis()
        if (ev["event"]?.jsonPrimitive?.content == "notification") {
            viewModelScope.launch { runCatching { Notifier.show(getApplication(), repo.syncNotifications()) } }
        }
    }

    fun bump() { _tick.value = System.currentTimeMillis() }
}

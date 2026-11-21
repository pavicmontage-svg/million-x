package com.titan.copyai.data

import com.titan.copyai.data.api.ApiClient
import com.titan.copyai.data.api.ApiService
import com.titan.copyai.data.api.Dashboard
import com.titan.copyai.data.api.NotificationDto
import com.titan.copyai.data.api.PairRequest
import com.titan.copyai.data.api.TitanJson
import com.titan.copyai.data.api.TraderRow
import com.titan.copyai.data.db.CacheEntry
import com.titan.copyai.data.db.NotificationEntity
import com.titan.copyai.data.db.TitanDatabase
import kotlinx.serialization.KSerializer
import kotlinx.serialization.builtins.ListSerializer

/** Cached result: `stale` = served from the Room cache because the server could not be reached. */
data class Cached<T>(val value: T, val updatedAt: Long, val stale: Boolean, val error: String? = null)

class Repository(val store: SecureStore, val client: ApiClient, val db: TitanDatabase) {
    val api: ApiService get() = client.api()

    suspend fun pair(serverUrl: String, code: String, deviceName: String) {
        val token = client.api(serverUrl).pair(PairRequest(code.trim(), deviceName)).token
        store.serverUrl = serverUrl
        store.token = token
    }

    suspend fun logout() {
        store.clear(); db.cache().clear(); db.notifications().clear()
    }

    private suspend fun <T> cached(key: String, ser: KSerializer<T>, fetch: suspend () -> T): Cached<T> {
        return try {
            val v = fetch()
            db.cache().put(CacheEntry(key, TitanJson.encodeToString(ser, v), System.currentTimeMillis()))
            Cached(v, System.currentTimeMillis(), false)
        } catch (e: Exception) {
            val c = db.cache().get(key) ?: throw e
            Cached(TitanJson.decodeFromString(ser, c.json), c.updatedAt, true, e.message)
        }
    }

    suspend fun dashboard() = cached("dashboard", Dashboard.serializer()) { api.dashboard() }
    suspend fun traders(market: String? = null) =
        cached("traders:${market ?: "all"}", ListSerializer(TraderRow.serializer())) { api.traders(market) }

    /** Pull new notifications into Room; returns the newly arrived ones (for system notifications). */
    suspend fun syncNotifications(): List<NotificationDto> {
        val after = db.notifications().maxId() ?: 0L
        val items = api.notifications(limit = 200, afterId = after)
        db.notifications().upsert(items.map {
            NotificationEntity(it.id, it.ts, it.kind, it.severity, it.title, it.body, it.read)
        })
        return items.filter { it.id > store.lastNotificationId }.also { n ->
            n.maxOfOrNull { it.id }?.let { store.lastNotificationId = maxOf(store.lastNotificationId, it) }
        }
    }
}

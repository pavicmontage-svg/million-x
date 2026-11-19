package com.millionx.ai

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import com.millionx.ai.data.Api
import com.millionx.ai.data.LiveSocket
import com.millionx.ai.data.SecureStore

class MillionXApp : Application() {
    lateinit var store: SecureStore
        private set
    lateinit var api: Api
        private set
    lateinit var socket: LiveSocket
        private set

    override fun onCreate() {
        super.onCreate()
        instance = this
        store = SecureStore(this)
        api = Api(store)
        socket = LiveSocket(api, store)
        val nm = getSystemService(NotificationManager::class.java)
        listOf(
            Triple("mx_critical", "Critical alerts", NotificationManager.IMPORTANCE_HIGH),
            Triple("mx_warning", "Warnings", NotificationManager.IMPORTANCE_HIGH),
            Triple("mx_info", "Trades and reports", NotificationManager.IMPORTANCE_DEFAULT),
        ).forEach { (id, name, imp) -> nm.createNotificationChannel(NotificationChannel(id, name, imp)) }
    }

    companion object {
        lateinit var instance: MillionXApp
            private set
    }
}

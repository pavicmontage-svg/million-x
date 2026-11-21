package com.titan.copyai

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import com.titan.copyai.data.Repository
import com.titan.copyai.data.SecureStore
import com.titan.copyai.data.api.ApiClient
import com.titan.copyai.data.db.TitanDatabase
import com.titan.copyai.work.NotificationWorker
import java.util.concurrent.TimeUnit

/** Manual dependency container (keeps the build free of annotation-processing DI). */
class TitanApp : Application() {
    lateinit var repo: Repository
        private set

    override fun onCreate() {
        super.onCreate()
        val store = SecureStore(this)
        repo = Repository(store, ApiClient(store), TitanDatabase.build(this))
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CH_CRITICAL, "Risk & emergency alerts",
            NotificationManager.IMPORTANCE_HIGH).apply { description = "Loss limits, drawdown, disconnections, emergency stop" })
        nm.createNotificationChannel(NotificationChannel(CH_TRADES, "Copied trades",
            NotificationManager.IMPORTANCE_DEFAULT).apply { description = "Opened / closed positions, SL changes, failures" })
        nm.createNotificationChannel(NotificationChannel(CH_INFO, "Traders & reports",
            NotificationManager.IMPORTANCE_LOW).apply { description = "Trader discovery, ranking, weekly report" })
        scheduleSync()
    }

    fun scheduleSync() {
        val req = PeriodicWorkRequestBuilder<NotificationWorker>(15, TimeUnit.MINUTES)
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .build()
        WorkManager.getInstance(this).enqueueUniquePeriodicWork("titan-sync", ExistingPeriodicWorkPolicy.KEEP, req)
    }

    companion object {
        const val CH_CRITICAL = "titan_critical"
        const val CH_TRADES = "titan_trades"
        const val CH_INFO = "titan_info"
    }
}

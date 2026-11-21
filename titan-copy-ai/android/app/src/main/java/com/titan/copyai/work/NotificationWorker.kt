package com.titan.copyai.work

import android.Manifest
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.titan.copyai.MainActivity
import com.titan.copyai.R
import com.titan.copyai.TitanApp
import com.titan.copyai.data.api.NotificationDto

/** Background sync (every 15 min, also triggered by the live socket): backend notifications -> Android notifications. */
class NotificationWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    override suspend fun doWork(): Result {
        val repo = (applicationContext as TitanApp).repo
        if (!repo.store.isPaired) return Result.success()
        return try {
            Notifier.show(applicationContext, repo.syncNotifications())
            Result.success()
        } catch (e: Exception) {
            Result.retry()
        }
    }
}

object Notifier {
    private val critical = setOf("daily_loss_limit", "weekly_loss_limit", "max_drawdown", "api_disconnected",
        "broker_disconnected", "copy_engine_stopped", "emergency_stop", "execution_failed")
    private val trades = setOf("position_opened", "position_closed", "stop_loss_modified")

    fun show(ctx: Context, items: List<NotificationDto>) {
        if (items.isEmpty()) return
        if (ContextCompat.checkSelfPermission(ctx, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return
        val pi = PendingIntent.getActivity(ctx, 0, Intent(ctx, MainActivity::class.java).putExtra("route", "notifications"),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val nm = NotificationManagerCompat.from(ctx)
        items.takeLast(10).forEach { n ->
            val channel = when (n.kind) {
                in critical -> TitanApp.CH_CRITICAL
                in trades -> TitanApp.CH_TRADES
                else -> TitanApp.CH_INFO
            }
            val notif = NotificationCompat.Builder(ctx, channel)
                .setSmallIcon(R.drawable.ic_stat_titan)
                .setContentTitle(n.title)
                .setContentText(n.body)
                .setStyle(NotificationCompat.BigTextStyle().bigText(n.body))
                .setPriority(if (channel == TitanApp.CH_CRITICAL) NotificationCompat.PRIORITY_HIGH else NotificationCompat.PRIORITY_DEFAULT)
                .setContentIntent(pi)
                .setAutoCancel(true)
                .build()
            try {
                nm.notify(n.id.toInt(), notif)
            } catch (_: SecurityException) { }
        }
    }
}

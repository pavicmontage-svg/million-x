package com.millionx.ai.push

import android.annotation.SuppressLint
import android.app.PendingIntent
import android.content.Intent
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import com.millionx.ai.MainActivity
import com.millionx.ai.MillionXApp
import com.millionx.ai.R
import com.millionx.ai.data.jsonBody
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

/** Receives Firebase pushes (trades, risk alerts) while the app is closed. */
class MxMessagingService : FirebaseMessagingService() {
    override fun onNewToken(token: String) {
        CoroutineScope(Dispatchers.IO).launch {
            runCatching { MillionXApp.instance.api.post("/api/devices/fcm", jsonBody("token" to token)) }
        }
    }

    @SuppressLint("MissingPermission")
    override fun onMessageReceived(message: RemoteMessage) {
        val level = message.data["level"] ?: "info"
        if (android.os.Build.VERSION.SDK_INT >= 33 && androidx.core.content.ContextCompat.checkSelfPermission(
                this, android.Manifest.permission.POST_NOTIFICATIONS) != android.content.pm.PackageManager.PERMISSION_GRANTED) return
        val n = message.notification
        val title = n?.title ?: message.data["title"] ?: "MILLION-X"
        val body = n?.body ?: message.data["body"] ?: ""
        val intent = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE
        )
        val channel = if (level in setOf("critical", "warning", "info")) "mx_$level" else "mx_info"
        val notif = NotificationCompat.Builder(this, channel)
            .setSmallIcon(R.drawable.ic_stat_mx)
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setPriority(if (level == "critical") NotificationCompat.PRIORITY_MAX else NotificationCompat.PRIORITY_DEFAULT)
            .setContentIntent(intent)
            .setAutoCancel(true)
            .build()
        runCatching { NotificationManagerCompat.from(this).notify(System.currentTimeMillis().toInt(), notif) }
    }
}

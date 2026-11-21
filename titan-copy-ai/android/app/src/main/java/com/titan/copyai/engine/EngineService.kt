package com.titan.copyai.engine

import android.annotation.SuppressLint
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.app.ServiceCompat
import com.titan.copyai.MainActivity
import com.titan.copyai.R
import com.titan.copyai.TitanApp
import com.titan.copyai.work.Notifier
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * Foreground service that keeps the on-phone engine running while the app is closed or the screen is
 * off: partial wake lock, START_STICKY (Android restarts it), [BootReceiver] after reboot/update.
 * Mirrors engine alerts (loss limits, fills, disconnections) as Android notifications.
 */
class EngineService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var wakeLock: PowerManager.WakeLock? = null
    private var started = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel(CHANNEL, "Engine running", NotificationManager.IMPORTANCE_LOW))
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            LocalEngine.stopPython(this)
            wakeLock?.let { if (it.isHeld) it.release() }
            ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
            stopSelf()
            return START_NOT_STICKY
        }
        val type = if (Build.VERSION.SDK_INT >= 34) ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE else 0
        ServiceCompat.startForeground(this, NOTIF_ID, notification("Starting the TITAN engine…"), type)
        if (!started) {
            started = true
            wakeLock = getSystemService(PowerManager::class.java)
                .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "titan:engine").apply { setReferenceCounted(false); acquire() }
            scope.launch {
                try {
                    LocalEngine.startPython(this@EngineService)
                } catch (e: Exception) {
                    LocalEngine.lastProblem = "Engine failed to start: ${e.message}"
                    update(LocalEngine.lastProblem)
                }
                monitor()
            }
        }
        return START_STICKY
    }

    private suspend fun monitor() {
        val repo = (application as TitanApp).repo
        while (scope.isActive) {
            val (ok, text) = LocalEngine.probe(this)
            if (!ok && !LocalEngine.pythonRunning(this)) {   // engine thread died: restart it
                runCatching { LocalEngine.startPython(this) }
            }
            LocalEngine.lastProblem = if (ok) "" else text
            update(if (ok) "Running on this phone · copy engine active" else "Engine problem: $text")
            if (ok && repo.store.isPaired) runCatching { Notifier.show(this, repo.syncNotifications()) }
            delay(if (ok) 30_000 else 10_000)
        }
    }

    private fun openApp(): PendingIntent =
        PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)

    private fun notification(text: String): Notification =
        NotificationCompat.Builder(this, CHANNEL).setSmallIcon(R.drawable.ic_stat_titan)
            .setContentTitle("TITAN Copy AI").setContentText(text).setOngoing(true)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setContentIntent(openApp()).setOnlyAlertOnce(true).build()

    @SuppressLint("MissingPermission")
    private fun update(text: String) {
        runCatching { NotificationManagerCompat.from(this).notify(NOTIF_ID, notification(text)) }
    }

    override fun onDestroy() {
        scope.cancel()
        wakeLock?.let { if (it.isHeld) it.release() }
        super.onDestroy()
    }

    companion object {
        const val CHANNEL = "titan_engine"
        const val NOTIF_ID = 7240
        const val ACTION_STOP = "com.titan.copyai.STOP_ENGINE"
    }
}

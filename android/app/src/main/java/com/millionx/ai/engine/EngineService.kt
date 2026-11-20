package com.millionx.ai.engine

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
import com.millionx.ai.MainActivity
import com.millionx.ai.MillionXApp
import com.millionx.ai.R
import com.millionx.ai.data.arr
import com.millionx.ai.data.bool
import com.millionx.ai.data.long
import com.millionx.ai.data.str
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * Foreground service that keeps the embedded trading engine alive while the app
 * is closed or the screen is off. A partial wake lock keeps the CPU running;
 * START_STICKY lets Android restart it; [BootReceiver] restarts it after reboot.
 * Engine alerts are mirrored as local notifications (no Firebase needed).
 */
class EngineService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var wakeLock: PowerManager.WakeLock? = null
    private var started = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel(CHANNEL, "Trading engine (running)", NotificationManager.IMPORTANCE_LOW))
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            LocalEngine.stopPython()
            wakeLock?.let { if (it.isHeld) it.release() }
            ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
            stopSelf()
            return START_NOT_STICKY
        }
        val type = if (Build.VERSION.SDK_INT >= 34) ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE else 0
        ServiceCompat.startForeground(this, NOTIF_ID, notification("Starting trading engine…"), type)
        if (!started) {
            started = true
            wakeLock = getSystemService(PowerManager::class.java)
                .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "millionx:engine").apply { setReferenceCounted(false); acquire() }
            scope.launch {
                try {
                    LocalEngine.startPython(this@EngineService)
                } catch (e: Exception) {
                    update("Engine failed to start: ${e.message}")
                    return@launch
                }
                monitor()
            }
        }
        return START_STICKY
    }

    private suspend fun monitor() {
        val app = MillionXApp.instance
        val prefs = getSharedPreferences("mx_local_engine", MODE_PRIVATE)
        while (scope.isActive) {
            val status = try {
                val h = LocalEngine.health()
                val mode = h.str("mode").uppercase()
                when {
                    h.bool("emergency_stop") -> "EMERGENCY STOP active · $mode"
                    !h.bool("engine_running") -> "Engine stopped · $mode"
                    h.bool("ok") -> "Running · $mode · ${h.long("market_symbols")} markets"
                    else -> "Degraded · $mode · ${h.str("last_errors").take(60)}"
                }
            } catch (e: Exception) {
                if (!LocalEngine.pythonRunning()) {  // server thread died: restart it
                    try { LocalEngine.startPython(this) } catch (_: Exception) { }
                }
                "Starting… (${e.message?.take(40)})"
            }
            update(status)
            if (app.store.token != null && app.store.serverUrl == LocalEngine.URL) mirrorAlerts(prefs)
            delay(30_000)
        }
    }

    @SuppressLint("MissingPermission")
    private suspend fun mirrorAlerts(prefs: android.content.SharedPreferences) {
        try {
            val list = MillionXApp.instance.api.get("/api/notifications?limit=20").arr
            val last = prefs.getLong("last_alert_id", -1L)
            val fresh = list.filter { it.long("id") > last }.sortedBy { it.long("id") }
            if (last >= 0) {
                fresh.filter { it.str("level") in setOf("critical", "warning") || it.str("category") in setOf("trade", "approval") }
                    .forEach { n ->
                        val ch = "mx_" + n.str("level").let { if (it in setOf("critical", "warning", "info")) it else "info" }
                        val notif = NotificationCompat.Builder(this, ch).setSmallIcon(R.drawable.ic_stat_mx)
                            .setContentTitle(n.str("title")).setContentText(n.str("body"))
                            .setStyle(NotificationCompat.BigTextStyle().bigText(n.str("body")))
                            .setContentIntent(openApp()).setAutoCancel(true).build()
                        runCatching { NotificationManagerCompat.from(this).notify(1000 + n.long("id").toInt(), notif) }
                    }
            }
            list.maxOfOrNull { it.long("id") }?.let { prefs.edit().putLong("last_alert_id", maxOf(it, last)).apply() }
        } catch (_: Exception) { }
    }

    private fun openApp(): PendingIntent =
        PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)

    private fun notification(text: String): Notification =
        NotificationCompat.Builder(this, CHANNEL).setSmallIcon(R.drawable.ic_stat_mx)
            .setContentTitle("MILLION-X engine").setContentText(text).setOngoing(true)
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
        const val CHANNEL = "mx_engine"
        const val NOTIF_ID = 42
        const val ACTION_STOP = "com.millionx.ai.STOP_ENGINE"
    }
}

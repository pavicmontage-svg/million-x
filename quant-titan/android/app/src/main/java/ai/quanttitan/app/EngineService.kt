package ai.quanttitan.app

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

/**
 * Foreground service that keeps the embedded trading platform alive with the screen off or the
 * app closed (partial wake lock, START_STICKY, restart after reboot via [BootReceiver]).
 * It also mirrors platform alerts (trades, stops, circuit breakers, emergency stop, pending
 * approvals, failures) into Android notifications.
 */
class EngineService : Service() {
    private var wakeLock: PowerManager.WakeLock? = null
    @Volatile private var loop: Thread? = null
    @Volatile private var alive = true

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CH_ENGINE, "Engine status", NotificationManager.IMPORTANCE_LOW))
        nm.createNotificationChannel(NotificationChannel(CH_ALERT, "Trading alerts", NotificationManager.IMPORTANCE_DEFAULT))
        nm.createNotificationChannel(NotificationChannel(CH_CRIT, "Critical risk alerts", NotificationManager.IMPORTANCE_HIGH))
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            alive = false
            Backend.stop()
            wakeLock?.let { if (it.isHeld) it.release() }
            ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
            stopSelf()
            return START_NOT_STICKY
        }
        val type = if (Build.VERSION.SDK_INT >= 34) ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE else 0
        ServiceCompat.startForeground(this, NOTIF_ID, engineNotification("Starting Quant Titan…"), type)
        if (wakeLock == null) {
            wakeLock = getSystemService(PowerManager::class.java)
                .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "quanttitan:engine").apply { setReferenceCounted(false); acquire() }
        }
        if (loop == null) {
            alive = true
            loop = Thread({ run() }, "qt-service").apply { isDaemon = true; start() }
        }
        return START_STICKY
    }

    private fun run() {
        try {
            Backend.start(this)
        } catch (e: Throwable) {
            Backend.lastError = e.message ?: e.toString()
            update("Engine failed to start: ${Backend.lastError.take(120)}")
        }
        val prefs = getSharedPreferences("qt", MODE_PRIVATE)
        while (alive) {
            val st = Backend.status(this)
            val text = when {
                st == null -> {
                    if (!Backend.running()) runCatching { Backend.start(this) }  // server thread died: restart it
                    "Not answering · ${Backend.pythonStatus().take(100)}"
                }
                else -> {
                    val pf = st.optJSONObject("portfolio")
                    val breakers = st.optJSONObject("breakers")?.keys()?.asSequence()?.toList().orEmpty()
                    val eq = pf?.optDouble("equity") ?: 0.0
                    val mode = "${st.optString("mode")}·${st.optString("venue")}"
                    when {
                        !st.optBoolean("running") -> "Runtime stopped · $mode · open the app to start"
                        breakers.isNotEmpty() -> "BLOCKED (${breakers.joinToString()}) · $mode · equity %.2f".format(eq)
                        st.optBoolean("paused") -> "Paused · $mode · equity %.2f".format(eq)
                        else -> "Running · $mode · equity %.2f · ${pf?.optInt("positions") ?: 0} pos".format(eq)
                    }
                }
            }
            update(text)
            mirrorAlerts(prefs)
            try { Thread.sleep(20_000) } catch (_: InterruptedException) { break }
        }
    }

    @SuppressLint("MissingPermission")
    private fun mirrorAlerts(prefs: android.content.SharedPreferences) {
        val last = prefs.getLong("last_alert_id", -1L)
        val arr = Backend.alerts(this, maxOf(last, 0L)) ?: return
        var maxId = last
        for (i in arr.length() - 1 downTo 0) {
            val a = arr.getJSONObject(i)
            val id = a.optLong("id")
            maxId = maxOf(maxId, id)
            if (last < 0) continue  // first run: do not replay history
            val sev = a.optString("severity")
            val kind = a.optString("kind")
            val notify = sev == "critical" || sev == "warning" || kind in setOf("trade_opened", "trade_closed", "stop_loss", "take_profit")
            if (!notify) continue
            val ch = if (sev == "critical") CH_CRIT else CH_ALERT
            val n = NotificationCompat.Builder(this, ch).setSmallIcon(R.drawable.ic_stat)
                .setContentTitle(kind.replace('_', ' ').uppercase())
                .setContentText(a.optString("message"))
                .setStyle(NotificationCompat.BigTextStyle().bigText(a.optString("message")))
                .setContentIntent(openApp()).setAutoCancel(true).build()
            runCatching { NotificationManagerCompat.from(this).notify(1000 + (id % 100000).toInt(), n) }
        }
        prefs.edit().putLong("last_alert_id", maxOf(maxId, 0L)).apply()
    }

    private fun openApp(): PendingIntent =
        PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)

    private fun engineNotification(text: String): Notification =
        NotificationCompat.Builder(this, CH_ENGINE).setSmallIcon(R.drawable.ic_stat)
            .setContentTitle("Quant Titan").setContentText(text).setOngoing(true)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setContentIntent(openApp()).setOnlyAlertOnce(true).build()

    @SuppressLint("MissingPermission")
    private fun update(text: String) {
        runCatching { NotificationManagerCompat.from(this).notify(NOTIF_ID, engineNotification(text)) }
    }

    override fun onDestroy() {
        alive = false
        loop?.interrupt()
        wakeLock?.let { if (it.isHeld) it.release() }
        super.onDestroy()
    }

    companion object {
        const val CH_ENGINE = "qt_engine"
        const val CH_ALERT = "qt_alerts"
        const val CH_CRIT = "qt_critical"
        const val NOTIF_ID = 7
        const val ACTION_STOP = "ai.quanttitan.app.STOP"
    }
}

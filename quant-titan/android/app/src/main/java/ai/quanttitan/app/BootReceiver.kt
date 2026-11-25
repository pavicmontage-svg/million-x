package ai.quanttitan.app

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** Restarts the engine after a reboot or an app update, unless the user stopped it. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val prefs = context.getSharedPreferences("qt", Context.MODE_PRIVATE)
        if (prefs.getBoolean("autostart", true)) {
            context.startForegroundService(Intent(context, EngineService::class.java))
        }
    }
}

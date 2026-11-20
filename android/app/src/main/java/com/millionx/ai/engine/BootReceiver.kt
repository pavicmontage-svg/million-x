package com.millionx.ai.engine

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** Restarts the local engine after a reboot or app update, if the owner enabled it. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action in setOf(Intent.ACTION_BOOT_COMPLETED, Intent.ACTION_MY_PACKAGE_REPLACED) &&
            LocalEngine.isEnabled(context)) {
            runCatching { LocalEngine.start(context) }
        }
    }
}

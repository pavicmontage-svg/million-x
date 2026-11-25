package ai.quanttitan.app

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout
import android.widget.TextView

/**
 * Hosts the Quant Titan control center (served by the embedded backend on 127.0.0.1) in a
 * WebView. Navigation is restricted to the local backend; other links open in the browser.
 */
class MainActivity : Activity() {
    private lateinit var web: WebView
    private lateinit var splash: TextView
    private val ui = Handler(Looper.getMainLooper())
    private var loaded = false
    private var waitedMs = 0

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val root = FrameLayout(this).apply { setBackgroundColor(Color.parseColor("#0B0F14")) }
        web = WebView(this).apply {
            setBackgroundColor(Color.parseColor("#0B0F14"))
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.allowFileAccess = false
            settings.allowContentAccess = false
            settings.mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            settings.setSupportMultipleWindows(false)
            webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(view: WebView, req: WebResourceRequest): Boolean {
                    val host = req.url.host ?: return true
                    if (host == "127.0.0.1" || host == "localhost") return false
                    runCatching { startActivity(Intent(Intent.ACTION_VIEW, req.url)) }
                    return true
                }
            }
        }
        if (applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0) WebView.setWebContentsDebuggingEnabled(true)
        splash = TextView(this).apply {
            setTextColor(Color.parseColor("#E6EDF3")); textSize = 16f; gravity = Gravity.CENTER
            setPadding(48, 48, 48, 48)
            text = "QUANT TITAN\n\nStarting the embedded engine…\n(first start unpacks Python, numpy, pandas, scipy — up to a minute)"
        }
        root.addView(web, FrameLayout.LayoutParams(-1, -1))
        root.addView(splash, FrameLayout.LayoutParams(-1, -1))
        setContentView(root)

        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        }
        getSharedPreferences("qt", MODE_PRIVATE).edit().putBoolean("autostart", true).apply()
        startForegroundService(Intent(this, EngineService::class.java))
        ui.post(::waitForBackend)
    }

    private fun waitForBackend() {
        if (loaded) return
        Thread {
            val ok = Backend.port != 0 && Backend.health(this)?.optBoolean("ok") == true
            ui.post {
                if (ok) {
                    loaded = true
                    web.loadUrl("http://127.0.0.1:${Backend.port}/#token=${Uri.encode(Backend.token(this))}")
                    splash.animate().alpha(0f).setDuration(250).withEndAction { splash.visibility = TextView.GONE }
                } else {
                    waitedMs += 1000
                    if (waitedMs % 10000 == 0) {
                        val err = Backend.lastError.ifEmpty { Backend.pythonStatus() }
                        splash.text = "QUANT TITAN\n\nStill starting… (${waitedMs / 1000}s)\n\n${err.take(400)}"
                    }
                    ui.postDelayed(::waitForBackend, 1000)
                }
            }
        }.start()
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        if (web.canGoBack()) web.goBack() else moveTaskToBack(true)  // keep the engine running
    }

    override fun onDestroy() {
        web.destroy()
        super.onDestroy()
    }
}

package io.legado.app.help.curfew

import android.app.Activity
import android.app.Application
import android.app.Dialog
import android.graphics.Color
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.Gravity
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.ImageButton
import android.widget.LinearLayout
import android.widget.TextView
import io.legado.app.BuildConfig
import io.legado.app.R
import io.legado.app.model.ReadAloud
import io.legado.app.service.BaseReadAloudService
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import okhttp3.Call
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import java.util.IdentityHashMap
import java.util.UUID
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicLong

object CurfewGate : Application.ActivityLifecycleCallbacks {
    private val enabled = BuildConfig.CURFEW_STATUS_URL.isNotBlank()
    private val main = Handler(Looper.getMainLooper())
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val epoch = AtomicLong()
    private val screens = IdentityHashMap<Activity, Pair<Dialog, TextView>>()
    private val started = mutableSetOf<Activity>()
    @Volatile private var lease: CurfewLease? = null
    @Volatile private var call: Call? = null
    private var checking = true

    fun blocked() = enabled && lease?.blocked(SystemClock.elapsedRealtime()) != false

    fun init(app: Application) {
        if (!enabled) return
        app.registerActivityLifecycleCallbacks(this)
        val client = OkHttpClient.Builder().followRedirects(false).followSslRedirects(false)
            .connectTimeout(3, TimeUnit.SECONDS).readTimeout(7, TimeUnit.SECONDS)
            .callTimeout(8, TimeUnit.SECONDS).build()
        scope.launch {
            while (true) {
                val requestEpoch = epoch.get()
                try {
                    val nonce = UUID.randomUUID().toString()
                    val url = BuildConfig.CURFEW_STATUS_URL.toHttpUrl().newBuilder()
                        .setQueryParameter("nonce", nonce)
                        .setQueryParameter("revision", lease?.revision.orEmpty()).build()
                    val request = Request.Builder().url(url).header("Cache-Control", "no-cache, no-store").build()
                    val pending = client.newCall(request)
                    call = pending
                    if (requestEpoch != epoch.get()) pending.cancel()
                    val value = pending.execute().use { response ->
                        check(response.isSuccessful)
                        CurfewLease.parse(response.body?.string().orEmpty(), nonce, SystemClock.elapsedRealtime())
                    }
                    main.post {
                        if (requestEpoch == epoch.get()) {
                            lease = value
                            checking = false
                            updateScreens(app)
                        }
                    }
                    // Let the main thread publish the revision before the next long poll.
                    delay(100)
                } catch (_: Exception) {
                    main.post {
                        if (requestEpoch == epoch.get()) {
                            lease = null
                            checking = false
                            updateScreens(app)
                        }
                    }
                    delay(1000)
                }
            }
        }
        main.post(object : Runnable {
            override fun run() {
                updateScreens(app)
                main.postDelayed(this, 250)
            }
        })
    }

    private fun updateScreens(app: Application) {
        val blocked = blocked()
        if (blocked && BaseReadAloudService.isRun) ReadAloud.stop(app)
        screens.forEach { (activity, screen) ->
            if (!activity.isFinishing && !activity.isDestroyed) {
                if (blocked) {
                    screen.second.setText(when {
                        checking -> R.string.curfew_checking
                        lease?.active == true -> R.string.curfew_active
                        else -> R.string.curfew_unavailable
                    })
                    if (!screen.first.isShowing) screen.first.show()
                    screen.first.window?.setLayout(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
                } else if (screen.first.isShowing) screen.first.dismiss()
            }
        }
    }

    override fun onActivityCreated(activity: Activity, savedInstanceState: Bundle?) {
        val label = TextView(activity).apply {
            textSize = 22f
            gravity = Gravity.CENTER
            setTextColor(Color.WHITE)
        }
        val layout = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setBackgroundColor(Color.rgb(25, 29, 28))
            addView(label, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
            addView(ImageButton(activity).apply {
                setImageResource(android.R.drawable.ic_menu_close_clear_cancel)
                contentDescription = activity.getString(R.string.curfew_exit)
                setOnClickListener { activity.moveTaskToBack(true) }
            })
        }
        val dialog = Dialog(activity, android.R.style.Theme_Material_NoActionBar).apply {
            setContentView(layout)
            setCancelable(false)
            setCanceledOnTouchOutside(false)
            window?.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        }
        // Protect task-switcher previews even when the normal reading screen is visible.
        activity.window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        screens[activity] = dialog to label
        updateScreens(activity.application)
    }

    override fun onActivityStarted(activity: Activity) {
        if (started.isEmpty()) {
            epoch.incrementAndGet()
            lease = null
            checking = true
            call?.cancel()
        }
        started.add(activity)
        updateScreens(activity.application)
    }

    override fun onActivityResumed(activity: Activity) = updateScreens(activity.application)
    override fun onActivityStopped(activity: Activity) { started.remove(activity) }
    override fun onActivityDestroyed(activity: Activity) {
        started.remove(activity)
        screens.remove(activity)?.first?.dismiss()
    }
    override fun onActivityPaused(activity: Activity) = Unit
    override fun onActivitySaveInstanceState(activity: Activity, outState: Bundle) = Unit
}

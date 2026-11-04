package io.legado.app.help.curfew

import android.app.Activity
import android.app.Application
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import io.legado.app.BuildConfig
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
import java.util.UUID
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicLong

object CurfewGate : Application.ActivityLifecycleCallbacks {
    private val enabled = BuildConfig.CURFEW_STATUS_URL.isNotBlank()
    private val main = Handler(Looper.getMainLooper())
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val epoch = AtomicLong()
    private val activities = mutableSetOf<Activity>()
    private val started = mutableSetOf<Activity>()
    @Volatile private var lease: CurfewLease? = null
    @Volatile private var call: Call? = null

    fun blocked() = enabled && lease?.blocked(SystemClock.elapsedRealtime()) == true

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
                            if (blocked()) kick(app)
                        }
                    }
                    // Let the main thread publish the revision before the next long poll.
                    delay(100)
                } catch (_: Exception) {
                    main.post {
                        if (requestEpoch == epoch.get()) {
                            lease = null
                        }
                    }
                    delay(1000)
                }
            }
        }
    }

    private fun kick(app: Application) {
        if (BaseReadAloudService.isRun) ReadAloud.stop(app)
        // Close the task, but keep synchronization alive for an early release.
        activities.toList().forEach { activity ->
            if (!activity.isFinishing && !activity.isDestroyed) {
                activity.finishAndRemoveTask()
            }
        }
    }

    override fun onActivityCreated(activity: Activity, savedInstanceState: Bundle?) {
        activities.add(activity)
    }

    override fun onActivityStarted(activity: Activity) {
        if (started.isEmpty()) {
            epoch.incrementAndGet()
            lease = null
            call?.cancel()
        }
        started.add(activity)
    }

    override fun onActivityResumed(activity: Activity) = Unit
    override fun onActivityStopped(activity: Activity) { started.remove(activity) }
    override fun onActivityDestroyed(activity: Activity) {
        started.remove(activity)
        activities.remove(activity)
    }
    override fun onActivityPaused(activity: Activity) = Unit
    override fun onActivitySaveInstanceState(activity: Activity, outState: Bundle) = Unit
}

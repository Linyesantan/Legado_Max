package io.legado.app.model.localBook

import android.util.AtomicFile
import androidx.annotation.Keep
import com.google.gson.Gson
import com.google.gson.JsonObject
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import splitties.init.appCtx
import java.io.File
import java.util.concurrent.TimeUnit

object CloudManuscript {
    @Keep // Gson constructs these models reflectively in the minified release APK.
    data class Snapshot(val schema: String, val chapter: Int, val title: String,
                        val raw: String, val revision: String, val proof: String)
    @Keep
    data class Draft(val endpoint: String, val snapshot: Snapshot, val body: String)
    @Keep
    data class Update(val schema: String, val base_revision: String, val after_sha256: String,
                      val proof: String, val changes: List<ManuscriptDelta.Change>)
    private val gson = Gson()
    private val client = OkHttpClient.Builder().followRedirects(false).followSslRedirects(false)
        .connectTimeout(15, TimeUnit.SECONDS).readTimeout(40, TimeUnit.SECONDS)
        .writeTimeout(40, TimeUnit.SECONDS).build()

    fun endpoint(chapterUrl: String): String? {
        val url = chapterUrl.toHttpUrlOrNull() ?: return null
        val match = Regex("^(.*)/c/([a-zA-Z0-9_-]+)/([0-9]+)\\.json$").matchEntire(url.encodedPath) ?: return null
        return url.newBuilder().encodedPath("${match.groupValues[1]}/manuscript/${match.groupValues[2]}/${match.groupValues[3]}")
            .query(null).fragment(null).build().toString()
    }

    private fun request(endpoint: String, headers: Map<String, String>, data: String? = null): JsonObject {
        val builder = Request.Builder().url(endpoint)
        headers.forEach { (name, value) -> builder.header(name, value) }
        if (data != null) builder.post(data.toRequestBody("application/json; charset=utf-8".toMediaType()))
        client.newCall(builder.build()).execute().use { response ->
            val text = response.body?.string().orEmpty()
            val json = runCatching { gson.fromJson(text, JsonObject::class.java) }.getOrNull()
            check(response.isSuccessful) {
                json?.get("error")?.asString ?: "AWS 回传连接失败（HTTP ${response.code}），草稿已保留"
            }
            return json ?: error("服务器没有返回有效改稿数据")
        }
    }

    fun fetch(endpoint: String, headers: Map<String, String>): Snapshot {
        val snapshot = gson.fromJson(request(endpoint, headers), Snapshot::class.java)
        check(snapshot.schema == "novel-delta/1" && snapshot.raw.startsWith("# ") && snapshot.raw.contains('\n')) {
            "书源不支持无损增量改稿"
        }
        return snapshot
    }

    fun newRaw(snapshot: Snapshot, body: String) = snapshot.raw.substringBefore('\n') + "\n" + body

    fun submit(endpoint: String, headers: Map<String, String>, snapshot: Snapshot, body: String): JsonObject {
        val raw = newRaw(snapshot, body)
        require(body.isNotBlank()) { "不能清空整章正文" }
        val update = Update("novel-delta/1", snapshot.revision, ManuscriptDelta.sha(raw), snapshot.proof,
            ManuscriptDelta.diff(snapshot.raw, raw))
        val result = request(endpoint, headers, gson.toJson(update))
        check(result.get("status")?.asString in listOf("applied", "duplicate", "unchanged")) { "服务器未确认写回，草稿已保留" }
        return result
    }

    private fun file(endpoint: String) = File(appCtx.filesDir, "cloud-manuscript/${ManuscriptDelta.sha(endpoint)}.json")

    @Synchronized fun draft(endpoint: String): Draft? {
        val file = file(endpoint)
        if (!file.exists() && !File(file.path + ".bak").exists()) return null
        return gson.fromJson(String(AtomicFile(file).readFully(), Charsets.UTF_8), Draft::class.java)
            .also { check(it.endpoint == endpoint) }
    }

    @Synchronized fun saveDraft(draft: Draft) {
        val file = file(draft.endpoint)
        file.parentFile!!.mkdirs()
        val atomic = AtomicFile(file)
        val stream = atomic.startWrite()
        try {
            stream.write(gson.toJson(draft).toByteArray(Charsets.UTF_8))
            atomic.finishWrite(stream)
        } catch (e: Throwable) {
            atomic.failWrite(stream)
            throw e
        }
    }

    @Synchronized fun clearDraft(endpoint: String) { AtomicFile(file(endpoint)).delete() }

    @Synchronized fun archiveDraft(endpoint: String) {
        val file = file(endpoint)
        if (file.exists()) file.copyTo(File(file.parentFile, "${file.name}.${System.currentTimeMillis()}.saved"))
        clearDraft(endpoint)
    }
}

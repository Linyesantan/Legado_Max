package io.legado.app.model.localBook

import com.google.gson.Gson
import com.google.gson.JsonObject
import org.junit.Assert.*
import org.junit.Test

class CloudManuscriptJsonTest {
    private val gson = Gson()

    @Test fun serverSnapshotAndDurableDraftRoundTrip() {
        val json = """{"schema":"novel-delta/1","chapter":1,"title":"测试","raw":"# 测试\n正文🙂\n","revision":"sha","proof":"signature"}"""
        val snapshot = gson.fromJson(json, CloudManuscript.Snapshot::class.java)
        assertEquals("# 测试\n正文🙂\n", snapshot.raw)
        val draft = CloudManuscript.Draft("https://example.org/manuscript/test/1", snapshot, "修改🙂\n")
        assertEquals(draft, gson.fromJson(gson.toJson(draft), CloudManuscript.Draft::class.java))
    }

    @Test fun outgoingDeltaUsesServerFieldNames() {
        val update = CloudManuscript.Update("novel-delta/1", "base", "after", "signature",
            listOf(ManuscriptDelta.Change(8, "旧", "新🙂")))
        val json = gson.fromJson(gson.toJson(update), JsonObject::class.java)
        assertEquals(setOf("schema", "base_revision", "after_sha256", "proof", "changes"), json.keySet())
        val change = json.getAsJsonArray("changes")[0].asJsonObject
        assertEquals(setOf("offset", "removed", "added"), change.keySet())
        assertEquals("新🙂", change.get("added").asString)
    }
}

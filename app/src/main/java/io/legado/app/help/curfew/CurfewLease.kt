package io.legado.app.help.curfew

import com.google.gson.Gson
import com.google.gson.JsonObject

/** Only a fresh, explicitly active observation can trigger a kick. */
data class CurfewLease(val active: Boolean, val expiresAt: Long, val revision: String) {
    fun blocked(now: Long) = active && now < expiresAt

    companion object {
        fun parse(body: String, nonce: String, receivedAt: Long): CurfewLease {
            val json = Gson().fromJson(body, JsonObject::class.java)
            require(json.get("schema")?.asString == "curfew/1")
            require(json.get("nonce")?.asString == nonce)
            val available = json.getAsJsonPrimitive("available")
            require(available.isBoolean && available.asBoolean)
            val active = json.getAsJsonPrimitive("active")
            require(active.isBoolean)
            val ttl = json.getAsJsonPrimitive("lease_ms")
            require(ttl.isNumber && ttl.asLong in 1000L..6000L)
            val revision = json.get("revision").asString
            require(revision.matches(Regex("[0-9a-f]{64}")))
            return CurfewLease(active.asBoolean, receivedAt + ttl.asLong, revision)
        }
    }
}

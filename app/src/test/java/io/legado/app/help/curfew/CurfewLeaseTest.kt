package io.legado.app.help.curfew

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class CurfewLeaseTest {
    private val revision = "a".repeat(64)
    private fun body(active: String = "false", available: String = "true", ttl: Long = 6000) =
        """{"schema":"curfew/1","nonce":"test","available":$available,"active":$active,"lease_ms":$ttl,"revision":"$revision"}"""

    @Test fun inactiveNeverBlocksEvenAfterExpiry() {
        val lease = CurfewLease.parse(body(), "test", 10000)
        assertFalse(lease.blocked(15999))
        assertFalse(lease.blocked(16000))
    }

    @Test fun freshActiveObservationBlocks() {
        assertTrue(CurfewLease.parse(body(active = "true"), "test", 10000).blocked(10000))
    }

    @Test fun expiredActiveObservationDoesNotBlock() {
        val lease = CurfewLease.parse(body(active = "true"), "test", 10000)
        assertTrue(lease.blocked(15999))
        assertFalse(lease.blocked(16000))
    }

    @Test fun unknownStatusDoesNotBlock() {
        val lease: CurfewLease? = null
        assertFalse(lease?.blocked(10000) == true)
    }

    @Test fun earlyReleaseReplacesActiveLease() {
        var lease = CurfewLease.parse(body(active = "true"), "test", 10000)
        assertTrue(lease.blocked(10001))
        lease = CurfewLease.parse(body(), "test", 10002)
        assertFalse(lease.blocked(10003))
    }

    @Test fun invalidResponsesCannotTriggerAKick() {
        val invalid = listOf(body(available = "false"), body(active = "true", available = "false"),
            body(active = "\"false\""),
            body(ttl = 0), body(ttl = 6001), body().replace("curfew/1", "curfew/2"),
            body().replace("test", "stale-nonce"), "{}", "not-json")
        invalid.forEach { assertTrue(runCatching { CurfewLease.parse(it, "test", 10000) }.isFailure) }
    }

    @Test fun resumedCurfewBlocksAfterEarlyRelease() {
        var lease = CurfewLease.parse(body(), "test", 10000)
        assertFalse(lease.blocked(10001))
        lease = CurfewLease.parse(body(active = "true"), "test", 10002)
        assertTrue(lease.blocked(10003))
    }
}

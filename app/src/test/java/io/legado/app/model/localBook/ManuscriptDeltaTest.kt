package io.legado.app.model.localBook

import org.junit.Assert.*
import org.junit.Test

class ManuscriptDeltaTest {
    private fun apply(old: String, edits: List<ManuscriptDelta.Change>): String {
        var result = old
        for (e in edits.reversed()) {
            val start = result.offsetByCodePoints(0, e.offset)
            check(result.substring(start).startsWith(e.removed))
            result = result.substring(0, start) + e.added + result.substring(start + e.removed.length)
        }
        return result
    }
    @Test fun sendsOnlyDeletedAndAddedCharacters() {
        val old="# 第1章 测试\n\n他🙂写了旧句。\n\n这一段原样保留。\n"
        val new=old.replace("旧","新")
        val changes=ManuscriptDelta.diff(old,new)
        assertEquals(1,changes.size)
        assertEquals("旧",changes.single().removed)
        assertEquals("新",changes.single().added)
        assertEquals(new,apply(old,changes))
    }
    @Test fun unicodeBlankLinesAndMultipleChanges() {
        val pairs=listOf("甲🙂乙\n\n丙" to "甲🦋乙\n\n新增\n丙", "aaaaabaaaa" to "aaaacaaa", "" to "新增", "删掉" to "", "原样" to "原样")
        pairs.forEach { (a,b) -> assertEquals(b,apply(a,ManuscriptDelta.diff(a,b))) }
    }
    @Test fun manyRandomEditsReconstructExactly() {
        val random=java.util.Random(42)
        repeat(200) {
            val a=(0 until random.nextInt(70)).joinToString("") { "甲乙丙丁\n "[random.nextInt(6)].toString() }
            val b=(0 until random.nextInt(70)).joinToString("") { "甲乙丙丁\n "[random.nextInt(6)].toString() }
            assertEquals(b,apply(a,ManuscriptDelta.diff(a,b)))
        }
    }
    @Test fun invalidSurrogateRejected() {
        assertThrows(IllegalArgumentException::class.java) { ManuscriptDelta.diff("a", "\uD800") }
    }
}

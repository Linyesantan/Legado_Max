package io.legado.app.model.localBook

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class RoundtripDocumentTest {
    private val doc = RoundtripDocument("a".repeat(32), listOf(
        RoundtripDocument.Chapter(1, "# 第1章 开始\n\n　原文  \n\nemoji🙂\n"),
        RoundtripDocument.Chapter(2, "# 第2章 接续\n\n末行无换行")
    ))

    @Test fun exactRoundtrip() {
        assertEquals(doc, RoundtripDocument.parse(doc.encode()))
        assertEquals(doc, RoundtripDocument.decode(("\uFEFF" + doc.encode().replace("\n", "\r\n")).toByteArray()))
    }

    @Test fun rejectBrokenBoundaryAndTitle() {
        for (text in listOf(doc.encode().replace("@@NOVEL:END:1@@", ""),
            doc.encode().replace("# 第2章 接续\n", ""), doc.encode().dropLast(5),
            doc.encode() + "多余正文", doc.copy(chapters = doc.chapters.reversed()).encode())) {
            assertThrows(Exception::class.java) { RoundtripDocument.parse(text) }
        }
    }
}

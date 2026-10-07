package io.legado.app.ui.book.read

import org.junit.Assert.assertEquals
import org.junit.Test

class ReadingEditPositionTest {
    @Test fun laterPageMatchesRawIndentationAndBlankLines() {
        val body = "第一段。\n\n　　这就是第二页正在读的句子。\n下一句。"
        assertEquals(body.indexOf("这就是"), ReadingEditPosition.locate(body, "这就是第二页\n正在读的句子。", 4))
    }

    @Test fun draftInsertionBeforePageDoesNotResetToStart() {
        val body = "手机草稿新增一大段。\n".repeat(10) + "当前阅读位置有足够多的文字用于匹配。"
        assertEquals(body.indexOf("当前"), ReadingEditPosition.locate(body, "当前阅读位置有足够多的文字用于匹配。", 0))
    }

    @Test fun repeatedPassageUsesNearestReadingPosition() {
        val sentence = "这句重复出现的文字需要定位到后一次。"
        val body = sentence + "\n中间文字\n" + sentence
        assertEquals(body.lastIndexOf(sentence), ReadingEditPosition.locate(body, sentence, sentence.length + 4))
    }

    @Test fun unicodeAndMissingAnchorStayAtSafeOffsets() {
        assertEquals(1, ReadingEditPosition.locate("甲🙂乙", "无法匹配", 2))
        assertEquals(0, ReadingEditPosition.locate("\n　", "", 99))
        assertEquals(3, ReadingEditPosition.locate("甲\n\n乙丙丁", "替换规则改变文字", 1))
    }
}

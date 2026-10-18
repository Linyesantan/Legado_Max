package io.legado.app.model.localBook

import java.nio.ByteBuffer
import java.nio.charset.CodingErrorAction

/** Plain UTF-8 transport. No trimming, paragraph formatting, or inferred boundaries. */
data class RoundtripDocument(val batch: String, val chapters: List<Chapter>) {
    data class Chapter(val number: Int, val raw: String) {
        val title: String get() = raw.substringBefore('\n').removePrefix("# ")
        val body: String get() = raw.substringAfter('\n')
    }

    fun encode(): String = buildString {
        append("NOVEL-ROUNDTRIP 1 $batch\n")
        chapters.forEach {
            append("@@NOVEL:BEGIN:${it.number}@@\n${it.raw}\n@@NOVEL:END:${it.number}@@\n")
        }
        append("@@NOVEL:EOF:$batch@@\n")
    }

    companion object {
        const val MAX_BYTES = 50 * 1024 * 1024
        fun decode(bytes: ByteArray): RoundtripDocument {
            require(bytes.size <= MAX_BYTES) { "改稿文件超过 50 MiB" }
            val text = Charsets.UTF_8.newDecoder()
                .onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT)
                .decode(ByteBuffer.wrap(bytes)).toString().removePrefix("\uFEFF")
                .replace("\r\n", "\n").replace("\r", "\n")
            return parse(text)
        }

        fun parse(text: String): RoundtripDocument {
            require(!text.contains('\uFEFF') && !text.contains('\u0000')) { "改稿文件含异常字符" }
            val head = Regex("^NOVEL-ROUNDTRIP 1 ([a-f0-9]{32})\n").find(text)
                ?: error("改稿批次标识损坏")
            val batch = head.groupValues[1]
            var cursor = head.range.last + 1
            val chapters = arrayListOf<Chapter>()
            while (!text.startsWith("@@NOVEL:EOF:", cursor)) {
                val begin = Regex("@@NOVEL:BEGIN:([0-9]+)@@\n").find(text, cursor)
                require(begin != null && begin.range.first == cursor) { "章节起始边界损坏" }
                val number = begin.groupValues[1].toInt()
                require(chapters.isEmpty() || number > chapters.last().number) { "第${number}章重复或顺序错误" }
                val start = begin.range.last + 1
                val endMarker = "\n@@NOVEL:END:$number@@\n"
                val stop = text.indexOf(endMarker, start)
                require(stop >= 0) { "第${number}章结束边界丢失" }
                val raw = text.substring(start, stop)
                require(!raw.contains("@@NOVEL:") && !raw.contains("NOVEL-ROUNDTRIP 1 ")) { "第${number}章边界混入正文" }
                require(Regex("^# [^\n]+\n").containsMatchIn(raw)) { "第${number}章标题丢失" }
                require(raw.substringAfter('\n').isNotBlank()) { "第${number}章正文为空" }
                chapters.add(Chapter(number, raw))
                cursor = stop + endMarker.length
            }
            require(chapters.isNotEmpty()) { "改稿文件没有章节" }
            require(text.substring(cursor) == "@@NOVEL:EOF:$batch@@\n") { "文件结束标识损坏或有额外内容" }
            return RoundtripDocument(batch, chapters)
        }
    }
}

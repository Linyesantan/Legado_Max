package io.legado.app.ui.book.read

import kotlin.math.abs

/** Match rendered page text against raw/draft text without changing either. Offsets are UTF-16. */
internal object ReadingEditPosition {
    fun compact(text: String) = text.filterNot { it.isWhitespace() }

    fun locate(body: String, pageText: String, precedingChars: Int): Int {
        val offsets = body.indices.filter { !body[it].isWhitespace() }
        if (offsets.isEmpty()) return 0
        val text = buildString { offsets.forEach { append(body[it]) } }
        val page = compact(pageText)
        val expected = precedingChars.coerceIn(0, text.length)
        for (size in listOf(48, 24, 12)) {
            for (skip in listOf(0, 24, 48)) {
                val anchor = page.drop(skip).take(size)
                if (anchor.length < minOf(size, 8)) continue
                var index = text.indexOf(anchor)
                var nearest = -1
                while (index >= 0) {
                    if (nearest < 0 || abs(index - skip - expected) < abs(nearest - skip - expected)) nearest = index
                    index = text.indexOf(anchor, index + 1)
                }
                if (nearest >= 0) return offsets[(nearest - skip).coerceIn(0, offsets.lastIndex)]
            }
        }
        val offset = offsets.getOrElse(expected) { body.length }
        return if (offset in 1 until body.length && body[offset].isLowSurrogate()) offset - 1 else offset
    }
}

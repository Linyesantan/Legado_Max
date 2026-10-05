package io.legado.app.model.localBook

import androidx.recyclerview.widget.DiffUtil
import java.security.MessageDigest

/** Offsets count Unicode code points, never UTF-16 halves. Equal text is not sent. */
object ManuscriptDelta {
    data class Change(val offset: Int, val removed: String, val added: String)

    private fun points(text: String): IntArray {
        val result = ArrayList<Int>()
        var i = 0
        while (i < text.length) {
            val ch = text[i]
            require(!ch.isLowSurrogate()) { "存在不完整的 Unicode 字符" }
            if (ch.isHighSurrogate()) {
                require(i + 1 < text.length && text[i + 1].isLowSurrogate()) { "存在不完整的 Unicode 字符" }
                result.add(Character.toCodePoint(ch, text[i + 1]))
                i += 2
            } else {
                result.add(ch.code)
                i++
            }
        }
        return result.toIntArray()
    }

    private fun slice(points: IntArray, start: Int, end: Int): String = buildString {
        for (i in start until end) appendCodePoint(points[i])
    }

    fun diff(old: String, new: String): List<Change> {
        val a = points(old)
        val b = points(new)
        require(a.size + b.size <= 200000) { "本章超过增量编辑长度上限" }
        val result = DiffUtil.calculateDiff(object : DiffUtil.Callback() {
            override fun getOldListSize() = a.size
            override fun getNewListSize() = b.size
            override fun areItemsTheSame(oldItemPosition: Int, newItemPosition: Int) =
                a[oldItemPosition] == b[newItemPosition]
            override fun areContentsTheSame(oldItemPosition: Int, newItemPosition: Int) = true
        }, false)
        val changes = arrayListOf<Change>()
        var oldStart = 0
        var newStart = 0
        for (i in a.indices) {
            val j = result.convertOldPositionToNew(i)
            if (j == DiffUtil.DiffResult.NO_POSITION) continue
            check(j >= newStart)
            if (oldStart < i || newStart < j) {
                changes.add(Change(oldStart, slice(a, oldStart, i), slice(b, newStart, j)))
            }
            oldStart = i + 1
            newStart = j + 1
        }
        if (oldStart < a.size || newStart < b.size) {
            changes.add(Change(oldStart, slice(a, oldStart, a.size), slice(b, newStart, b.size)))
        }
        return changes
    }

    fun sha(text: String): String {
        points(text) // Reject malformed surrogate sequences before encoding.
        return MessageDigest.getInstance("SHA-256").digest(text.toByteArray(Charsets.UTF_8))
            .joinToString("") { "%02x".format(it.toInt() and 255) }
    }
}

package io.legado.app.model.localBook

import android.util.AtomicFile
import io.legado.app.data.entities.Book
import io.legado.app.data.entities.BookChapter
import io.legado.app.help.book.isLocalTxt
import io.legado.app.utils.MD5Utils
import splitties.init.appCtx
import java.io.File

/** Durable whole-document drafts, independent of Legado's disposable chapter cache. */
object NovelRoundtrip {
    private fun draft(book: Book) = File(appCtx.filesDir,
        "novel-roundtrip/${MD5Utils.md5Encode16(book.bookUrl)}.txt")

    @Synchronized
    fun load(book: Book): RoundtripDocument? {
        if (!book.isLocalTxt) return null
        val draft = draft(book)
        if (draft.exists() || File(draft.path + ".bak").exists()) {
            return RoundtripDocument.decode(AtomicFile(draft).readFully())
        }
        return LocalBook.getBookInputStream(book).buffered().use { stream ->
            stream.mark(64)
            val probe = ByteArray(48)
            val count = stream.read(probe)
            stream.reset()
            if (count <= 0 || !String(probe, 0, count, Charsets.UTF_8)
                    .removePrefix("\uFEFF").startsWith("NOVEL-ROUNDTRIP")) return@use null
            val bytes = stream.readBytes()
            RoundtripDocument.decode(bytes)
        }
    }

    @Synchronized
    fun toc(book: Book): ArrayList<BookChapter>? {
        val doc = load(book) ?: return null
        book.charset = "UTF-8"
        return ArrayList(doc.chapters.mapIndexed { index, chapter ->
            BookChapter(bookUrl = book.bookUrl, index = index, title = chapter.title,
                url = "novel-roundtrip:${doc.batch}:${chapter.number}", start = 0, end = 0)
        })
    }

    fun chapter(doc: RoundtripDocument, chapter: BookChapter): RoundtripDocument.Chapter {
        val entry = doc.chapters.getOrNull(chapter.index) ?: error("章节索引不匹配，请重新导入")
        require(chapter.url == "novel-roundtrip:${doc.batch}:${entry.number}") { "章节批次不匹配，请重新导入" }
        return entry
    }

    @Synchronized
    fun save(book: Book, chapter: BookChapter, expected: String, body: String): File {
        val doc = load(book) ?: error("不是改稿文件")
        val old = this.chapter(doc, chapter)
        require(old.raw == expected) { "这一章已在另一窗口修改，请重新打开" }
        val raw = old.raw.substringBefore('\n') + "\n" + body
        val updated = doc.copy(chapters = doc.chapters.map {
            if (it.number == old.number) it.copy(raw = raw) else it
        })
        val text = updated.encode()
        require(text.toByteArray(Charsets.UTF_8).size <= RoundtripDocument.MAX_BYTES) { "改稿文件超过 50 MiB" }
        RoundtripDocument.parse(text)
        val file = draft(book)
        file.parentFile!!.mkdirs()
        val atomic = AtomicFile(file)
        val stream = atomic.startWrite()
        try {
            stream.write(text.toByteArray(Charsets.UTF_8))
            atomic.finishWrite(stream)
        } catch (e: Throwable) {
            atomic.failWrite(stream)
            throw e
        }
        check(AtomicFile(file).readFully().contentEquals(text.toByteArray(Charsets.UTF_8))) { "改稿保存校验失败" }
        return file
    }

    @Synchronized
    fun shareFile(book: Book): File {
        val doc = load(book) ?: error("不是改稿文件")
        // 每次分享使用独立文件，分享途中继续改稿不会改变已选择的附件。
        val dir = File(appCtx.filesDir, "novel-roundtrip/share/${java.util.UUID.randomUUID()}")
        check(dir.mkdirs())
        return File(dir, "dushi-${doc.batch}.txt").apply { writeText(doc.encode(), Charsets.UTF_8) }
    }
}

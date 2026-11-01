package io.legado.app.model.webBook

import io.legado.app.data.entities.Book
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class TocPageCacheTest {
    @Test
    fun detailResponseIsOnlyUsedOnce() {
        val book = Book(bookUrl = "https://example.com/book/draft.json",
            tocUrl = "https://example.com/book/draft.json")
        book.tocHtml = "old chapters"

        assertEquals("old chapters", book.takeTocHtml())
        assertNull(book.tocHtml)
        // A subsequent refresh must fetch the server, including any newly added chapters.
        assertNull(book.takeTocHtml())
    }

    @Test
    fun differentTocUrlDiscardsOldDetailResponse() {
        val book = Book(bookUrl = "https://example.com/book",
            tocUrl = "https://example.com/toc")
        book.tocHtml = "stale detail response"

        assertNull(book.takeTocHtml())
        assertNull(book.tocHtml)
    }

    @Test
    fun freshDetailResponseCanSeedNextLoad() {
        val book = Book(bookUrl = "https://example.com/book",
            tocUrl = "https://example.com/book")
        book.tocHtml = "first response"
        book.takeTocHtml()
        book.tocHtml = "new chapters"

        assertEquals("new chapters", book.takeTocHtml())
        assertNull(book.takeTocHtml())
    }

    @Test
    fun emptyOrMissingResponseRequiresNetwork() {
        val book = Book(bookUrl = "https://example.com/book",
            tocUrl = "https://example.com/book")
        assertNull(book.takeTocHtml())
        book.tocHtml = ""
        assertNull(book.takeTocHtml())
        assertNull(book.tocHtml)
    }
}

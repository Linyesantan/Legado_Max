package io.legado.app.model.webBook

import io.legado.app.data.entities.Book

/** The detail response can seed the first TOC load, but must not cache later refreshes. */
internal fun Book.takeTocHtml(): String? = synchronized(this) {
    val html = tocHtml
    tocHtml = null
    html?.takeIf { bookUrl == tocUrl && it.isNotEmpty() }
}

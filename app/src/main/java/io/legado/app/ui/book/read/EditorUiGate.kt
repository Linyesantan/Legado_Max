package io.legado.app.ui.book.read

import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.isActive

/** A cancelled blocking request may still throw IOException instead of CancellationException. */
internal suspend fun updateEditorUi(isViewActive: () -> Boolean, update: () -> Unit) {
    if (currentCoroutineContext().isActive && isViewActive()) update()
}

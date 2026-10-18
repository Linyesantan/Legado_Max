package io.legado.app.ui.book.read

import java.io.IOException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withContext
import org.junit.Assert.assertEquals
import org.junit.Test

class EditorUiGateTest {
    @Test fun cancelledBlockingRequestCannotShowErrorOrTouchViews() = runBlocking {
        val started = CompletableDeferred<Unit>()
        val response = CompletableDeferred<Unit>()
        var touches = 0
        val job = launch {
            try {
                withContext(NonCancellable) {
                    started.complete(Unit)
                    response.await()
                    throw IOException("late network failure")
                }
            } catch (e: IOException) {
                updateEditorUi({ true }) { touches++ }
            } finally {
                updateEditorUi({ true }) { touches++ }
            }
        }
        started.await()
        job.cancel()
        response.complete(Unit)
        job.join()
        assertEquals(0, touches)
    }

    @Test fun detachedViewRejectsLateSuccessAndFailure() = runBlocking {
        var attached = true
        var touches = 0
        val started = CompletableDeferred<Unit>()
        val response = CompletableDeferred<Unit>()
        val job = launch {
            started.complete(Unit)
            response.await()
            updateEditorUi({ attached }) { touches++ }
            try { throw IOException("late failure") }
            catch (e: IOException) { updateEditorUi({ attached }) { touches++ } }
            finally { updateEditorUi({ attached }) { touches++ } }
        }
        started.await()
        attached = false
        response.complete(Unit)
        job.join()
        assertEquals(0, touches)
    }

    @Test fun currentViewStillReceivesUpdates() = runBlocking {
        var touches = 0
        updateEditorUi({ true }) { touches++ }
        assertEquals(1, touches)
    }
}

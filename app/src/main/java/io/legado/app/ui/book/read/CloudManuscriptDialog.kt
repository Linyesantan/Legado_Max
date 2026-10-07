package io.legado.app.ui.book.read

import android.app.Dialog
import android.os.Bundle
import android.view.MenuItem
import android.view.View
import android.view.ViewGroup
import androidx.appcompat.app.AlertDialog
import androidx.activity.ComponentDialog
import androidx.activity.addCallback
import androidx.core.widget.addTextChangedListener
import androidx.fragment.app.viewModels
import androidx.lifecycle.ViewModel
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import io.legado.app.R
import io.legado.app.base.BaseDialogFragment
import io.legado.app.data.entities.Book
import io.legado.app.data.entities.BookChapter
import io.legado.app.databinding.DialogContentEditBinding
import io.legado.app.help.book.BookHelp
import io.legado.app.model.ReadBook
import io.legado.app.model.localBook.CloudManuscript
import io.legado.app.utils.setLayout
import io.legado.app.utils.toastOnUi
import io.legado.app.utils.viewbindingdelegate.viewBinding
import kotlinx.coroutines.Dispatchers.IO
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** Raw text editor for the user's online manuscript, with durable retryable drafts. */
class CloudManuscriptDialog : BaseDialogFragment(R.layout.dialog_content_edit) {
    private val binding by viewBinding(DialogContentEditBinding::bind)
    private val model by viewModels<EditorState>()
    private var busy = false
    private var operation: Job? = null
    private var closing = false

    private suspend fun updateUi(editorView: View, update: (android.content.Context) -> Unit) {
        updateEditorUi({
            isAdded && view === editorView && !isStateSaved &&
                viewLifecycleOwnerLiveData.value?.lifecycle?.currentState?.let {
                    it != Lifecycle.State.DESTROYED
                } == true
        }) {
            context?.let(update)
        }
    }

    override fun onCreateDialog(savedInstanceState: Bundle?): Dialog =
        ComponentDialog(requireContext(), theme).apply {
            onBackPressedDispatcher.addCallback(this@CloudManuscriptDialog) { close() }
        }

    class EditorState : ViewModel() {
        var endpoint: String? = null
        var headers: Map<String, String> = emptyMap()
        var snapshot: CloudManuscript.Snapshot? = null
        var body: String = ""
        var book: Book? = null
        var chapter: BookChapter? = null
        var pageText: String? = null
        var precedingChars = 0
    }

    override fun onStart() {
        super.onStart()
        setLayout(1f, ViewGroup.LayoutParams.MATCH_PARENT)
        isCancelable = true
        dialog?.setCanceledOnTouchOutside(false)
    }

    override fun onFragmentCreated(view: View, savedInstanceState: Bundle?) {
        if (model.pageText == null) {
            val chapter = ReadBook.curTextChapter
            val pageIndex = ReadBook.durPageIndex
            val page = chapter?.getPage(pageIndex)
            model.pageText = page?.lines?.filter { !it.isTitle }?.joinToString("") { it.text }.orEmpty()
            model.precedingChars = chapter?.pages?.take(pageIndex)?.sumOf { previous ->
                previous.lines.filter { !it.isTitle }.sumOf { ReadingEditPosition.compact(it.text).length }
            } ?: ReadBook.durChapterPos
        }
        binding.toolBar.title = "编辑正文"
        binding.toolBar.setNavigationIcon(R.drawable.ic_arrow_back)
        binding.toolBar.navigationContentDescription = "返回"
        binding.toolBar.setNavigationOnClickListener { close() }
        binding.toolBar.subtitle = "保存后经 AWS 回传到 VM"
        binding.toolBar.menu.add(0, 1, 0, "保存并回传").setShowAsAction(MenuItem.SHOW_AS_ACTION_ALWAYS)
        binding.toolBar.menu.add(0, 2, 1, "关闭（保留草稿）")
        binding.toolBar.menu.add(0, 3, 2, "重新加载 VM 原文")
        binding.toolBar.menu.add(0, 4, 3, "回到阅读位置")
        binding.toolBar.setOnMenuItemClickListener {
            if (it.itemId == 2) close()
            else if (!busy) when (it.itemId) {
                1 -> save()
                3 -> AlertDialog.Builder(requireContext()).setMessage("重新加载 VM 原文？当前草稿会保留备份。")
                    .setNegativeButton("取消", null).setPositiveButton("重新加载") { _, _ -> load(true) }.show()
                4 -> scrollToReadingPosition(view)
            }
            true
        }
        binding.contentView.addTextChangedListener { if (!busy) model.body = it?.toString().orEmpty() }
        load(false)
    }

    private fun scrollToReadingPosition(editorView: View) {
        val editor = binding.contentView
        val offset = ReadingEditPosition.locate(editor.text?.toString().orEmpty(),
            model.pageText.orEmpty(), model.precedingChars)
        editor.post {
            if (!isAdded || view !== editorView || closing) return@post
            val layout = editor.layout ?: return@post
            val cursor = offset.coerceIn(0, editor.text?.length ?: 0)
            editor.setSelection(cursor)
            val line = layout.getLineForOffset(cursor)
            editor.scrollTo(0, (layout.getLineTop(line) - editor.height / 4).coerceAtLeast(0))
        }
    }

    private fun setBusy(value: Boolean) {
        busy = value
        binding.contentView.isEnabled = !value && model.snapshot != null
        binding.toolBar.menu.findItem(1).isEnabled = !value && model.snapshot != null
        binding.toolBar.menu.findItem(3).isEnabled = !value && model.endpoint != null
        binding.toolBar.menu.findItem(4).isEnabled = !value && model.snapshot != null
        binding.rlLoading.visibility = if (value) View.VISIBLE else View.GONE
    }

    private fun load(reload: Boolean) {
        val editorView = view ?: return
        setBusy(true)
        operation = viewLifecycleOwner.lifecycleScope.launch {
            try {
                if (model.book == null) {
                    model.book = ReadBook.book ?: error("书籍尚未打开")
                    model.chapter = ReadBook.curTextChapter?.chapter ?: error("章节尚未加载")
                    model.endpoint = CloudManuscript.endpoint(model.chapter!!.url) ?: error("这本书没有 VM 改稿入口")
                    model.headers = withContext(IO) { ReadBook.bookSource?.getHeaderMap(true) ?: emptyMap() }
                }
                if (model.snapshot == null || reload) {
                    val endpoint = model.endpoint!!
                    val draft = withContext(IO) {
                        if (reload) {
                            model.snapshot?.let { CloudManuscript.saveDraft(CloudManuscript.Draft(endpoint, it, model.body)) }
                            val snapshot = CloudManuscript.fetch(endpoint, model.headers)
                            CloudManuscript.archiveDraft(endpoint)
                            CloudManuscript.Draft(endpoint, snapshot, snapshot.raw.substringAfter('\n'))
                        } else CloudManuscript.draft(endpoint) ?: CloudManuscript.fetch(endpoint, model.headers).let {
                            CloudManuscript.Draft(endpoint, it, it.raw.substringAfter('\n'))
                        }
                    }
                    model.snapshot = draft.snapshot
                    model.body = draft.body
                }
                updateUi(editorView) {
                    binding.toolBar.title = model.snapshot!!.title
                    binding.contentView.setText(model.body)
                    scrollToReadingPosition(editorView)
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                updateUi(editorView) { ctx ->
                    ctx.toastOnUi(e.localizedMessage ?: "加载失败")
                    binding.toolBar.subtitle = "加载失败，可关闭后重试"
                }
            } finally { updateUi(editorView) { setBusy(false) } }
        }
    }

    private fun save() {
        val editorView = view ?: return
        val snapshot = model.snapshot ?: return
        val endpoint = model.endpoint ?: return
        val body = binding.contentView.text?.toString().orEmpty()
        model.body = body
        setBusy(true)
        operation = viewLifecycleOwner.lifecycleScope.launch {
            var confirmed = false
            try {
                val result = withContext(IO) {
                    CloudManuscript.saveDraft(CloudManuscript.Draft(endpoint, snapshot, body))
                    CloudManuscript.submit(endpoint, model.headers, snapshot, body)
                }
                confirmed = true
                // 只有服务器确认后才更新阅读缓存；上传失败时保留可重试草稿。
                withContext(IO) {
                    // 响应丢失后的重试可能遇到更晚的新稿，回读服务器，不用旧请求覆盖阅读缓存。
                    val latest = CloudManuscript.fetch(endpoint, model.headers)
                    model.chapter!!.title = latest.title
                    model.chapter!!.update()
                    BookHelp.saveText(model.book!!, model.chapter!!, latest.raw.removePrefix("# "))
                    CloudManuscript.clearDraft(endpoint)
                }
                updateUi(editorView) { ctx ->
                    ReadBook.loadContent(model.chapter!!.index, resetPageOffset = false)
                    val totals = result.getAsJsonObject("totals")
                    ctx.toastOnUi(if (totals != null && totals.has("added_chars"))
                        "已回传 VM：+${totals.get("added_chars").asInt}字 -${totals.get("removed_chars").asInt}字"
                        else "VM 已确认保存")
                    dismiss()
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                updateUi(editorView) { ctx ->
                    binding.toolBar.subtitle = if (confirmed) "VM 已保存，刷新失败；草稿已保留" else "回传未完成，草稿已保留"
                    AlertDialog.Builder(ctx).setTitle(if (confirmed) "已保存，可重试刷新" else "未确认回传成功")
                        .setMessage(e.localizedMessage ?: "请检查网络后重试")
                        .setPositiveButton("知道了", null).show()
                }
            } finally { updateUi(editorView) { setBusy(false) } }
        }
    }

    private fun close() {
        val editorView = view ?: return
        if (!isAdded || isStateSaved) return
        if (closing) return
        closing = true
        operation?.cancel()
        val snapshot = model.snapshot
        if (snapshot == null) { dismiss(); return }
        model.body = binding.contentView.text?.toString().orEmpty()
        setBusy(true)
        viewLifecycleOwner.lifecycleScope.launch {
            try {
                withContext(IO) { CloudManuscript.saveDraft(CloudManuscript.Draft(model.endpoint!!, snapshot, model.body)) }
                updateUi(editorView) { dismiss() }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                updateUi(editorView) { ctx ->
                    closing = false
                    ctx.toastOnUi("草稿保存失败：${e.localizedMessage}")
                }
            } finally { updateUi(editorView) { setBusy(false) } }
        }
    }
}

package io.legado.app.ui.book.read

import android.os.Bundle
import android.view.MenuItem
import android.view.View
import android.view.ViewGroup
import androidx.appcompat.app.AlertDialog
import androidx.core.widget.addTextChangedListener
import androidx.fragment.app.viewModels
import androidx.lifecycle.ViewModel
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
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** Raw text editor for the user's online manuscript, with durable retryable drafts. */
class CloudManuscriptDialog : BaseDialogFragment(R.layout.dialog_content_edit) {
    private val binding by viewBinding(DialogContentEditBinding::bind)
    private val model by viewModels<EditorState>()
    private var busy = false

    class EditorState : ViewModel() {
        var endpoint: String? = null
        var headers: Map<String, String> = emptyMap()
        var snapshot: CloudManuscript.Snapshot? = null
        var body: String = ""
        var book: Book? = null
        var chapter: BookChapter? = null
    }

    override fun onStart() {
        super.onStart()
        setLayout(1f, ViewGroup.LayoutParams.MATCH_PARENT)
        isCancelable = false
    }

    override fun onFragmentCreated(view: View, savedInstanceState: Bundle?) {
        binding.toolBar.title = "编辑正文"
        binding.toolBar.subtitle = "保存后经 AWS 回传到 VM"
        binding.toolBar.menu.add(0, 1, 0, "保存并回传").setShowAsAction(MenuItem.SHOW_AS_ACTION_ALWAYS)
        binding.toolBar.menu.add(0, 2, 1, "关闭（保留草稿）")
        binding.toolBar.menu.add(0, 3, 2, "重新加载 VM 原文")
        binding.toolBar.setOnMenuItemClickListener {
            if (!busy) when (it.itemId) {
                1 -> save()
                2 -> close()
                3 -> AlertDialog.Builder(requireContext()).setMessage("重新加载 VM 原文？当前草稿会保留备份。")
                    .setNegativeButton("取消", null).setPositiveButton("重新加载") { _, _ -> load(true) }.show()
            }
            true
        }
        binding.contentView.addTextChangedListener { if (!busy) model.body = it?.toString().orEmpty() }
        load(false)
    }

    private fun setBusy(value: Boolean) {
        busy = value
        binding.contentView.isEnabled = !value && model.snapshot != null
        binding.toolBar.menu.findItem(1).isEnabled = !value && model.snapshot != null
        binding.toolBar.menu.findItem(2).isEnabled = !value
        binding.toolBar.menu.findItem(3).isEnabled = !value && model.endpoint != null
        binding.rlLoading.visibility = if (value) View.VISIBLE else View.GONE
    }

    private fun load(reload: Boolean) {
        setBusy(true)
        lifecycleScope.launch {
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
                binding.toolBar.title = model.snapshot!!.title
                binding.contentView.setText(model.body)
            } catch (e: Exception) {
                requireContext().toastOnUi(e.localizedMessage ?: "加载失败")
                binding.toolBar.subtitle = "加载失败，可关闭后重试"
            } finally { if (view != null) setBusy(false) }
        }
    }

    private fun save() {
        val snapshot = model.snapshot ?: return
        val endpoint = model.endpoint ?: return
        val body = binding.contentView.text?.toString().orEmpty()
        model.body = body
        setBusy(true)
        lifecycleScope.launch {
            try {
                val result = withContext(IO) {
                    CloudManuscript.saveDraft(CloudManuscript.Draft(endpoint, snapshot, body))
                    CloudManuscript.submit(endpoint, model.headers, snapshot, body)
                }
                // 只有服务器确认后才更新阅读缓存；上传失败时保留可重试草稿。
                withContext(IO) {
                    BookHelp.saveText(model.book!!, model.chapter!!, CloudManuscript.newRaw(snapshot, body).removePrefix("# "))
                    CloudManuscript.clearDraft(endpoint)
                }
                ReadBook.loadContent(model.chapter!!.index, resetPageOffset = false)
                val totals = result.getAsJsonObject("totals")
                requireContext().toastOnUi(if (totals != null && totals.has("added_chars"))
                    "已回传 VM：+${totals.get("added_chars").asInt}字 -${totals.get("removed_chars").asInt}字"
                    else "VM 已确认保存")
                dismiss()
            } catch (e: Exception) {
                binding.toolBar.subtitle = "回传未完成，草稿已保留"
                AlertDialog.Builder(requireContext()).setTitle("未确认回传成功")
                    .setMessage(e.localizedMessage ?: "请检查网络后重试")
                    .setPositiveButton("知道了", null).show()
            } finally { if (view != null) setBusy(false) }
        }
    }

    private fun close() {
        val snapshot = model.snapshot
        if (snapshot == null) { dismiss(); return }
        model.body = binding.contentView.text?.toString().orEmpty()
        setBusy(true)
        lifecycleScope.launch {
            try {
                withContext(IO) { CloudManuscript.saveDraft(CloudManuscript.Draft(model.endpoint!!, snapshot, model.body)) }
                dismiss()
            } catch (e: Exception) {
                requireContext().toastOnUi("草稿保存失败：${e.localizedMessage}")
            } finally { if (view != null) setBusy(false) }
        }
    }
}

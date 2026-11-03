package io.legado.app.help.curfew

import android.app.Activity
import android.graphics.Color
import android.os.Bundle
import android.view.Gravity
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.ImageButton
import android.widget.LinearLayout
import android.widget.TextView
import io.legado.app.R

/** A real foreground screen keeps reader dialogs and lifecycle callbacks behind the lock. */
class CurfewLockActivity : Activity() {
    private lateinit var message: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        message = TextView(this).apply {
            textSize = 22f
            gravity = Gravity.CENTER
            setTextColor(Color.WHITE)
            setText(R.string.curfew_checking)
        }
        setContentView(LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setBackgroundColor(Color.rgb(25, 29, 28))
            addView(message, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
            val size = (48 * resources.displayMetrics.density).toInt()
            addView(ImageButton(this@CurfewLockActivity).apply {
                setImageResource(android.R.drawable.ic_menu_close_clear_cancel)
                contentDescription = getString(R.string.curfew_exit)
                setOnClickListener { moveTaskToBack(true) }
            }, LinearLayout.LayoutParams(size, size))
        })
    }

    fun updateMessage(resource: Int) {
        if (::message.isInitialized) message.setText(resource)
    }

    @Deprecated("The curfew gate cannot be dismissed with Back")
    override fun onBackPressed() {
        moveTaskToBack(true)
    }
}

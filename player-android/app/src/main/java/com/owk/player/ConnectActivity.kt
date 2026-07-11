package com.owk.player

import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import android.text.Editable
import android.text.TextWatcher
import android.view.Gravity
import android.view.KeyEvent
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.isVisible
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import org.json.JSONArray

class ConnectActivity : AppCompatActivity() {

    private lateinit var scrollView: ScrollView
    private lateinit var historySection: LinearLayout
    private lateinit var discoverySection: LinearLayout
    private lateinit var scanStatus: TextView
    private lateinit var ipInput: EditText
    private lateinit var connectBtn: Button

    private val discovered = mutableListOf<DiscoveredServer>()
    private val allFocusable = mutableListOf<View>()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_connect)

        scrollView = findViewById(R.id.scrollView)
        historySection = findViewById(R.id.historySection)
        discoverySection = findViewById(R.id.discoverySection)
        scanStatus = findViewById(R.id.scanStatus)
        ipInput = findViewById(R.id.ipInput)
        connectBtn = findViewById(R.id.connectBtn)

        loadHistory()
        startDiscovery()

        ipInput.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {}
            override fun afterTextChanged(s: Editable?) {
                connectBtn.isEnabled = s?.matches(Regex("^\\d{1,3}(\\.\\d{1,3}){3}$")) == true
            }
        })

        ipInput.setOnEditorActionListener { _, action, _ ->
            if (action == android.view.inputmethod.EditorInfo.IME_ACTION_GO && connectBtn.isEnabled) {
                doConnect(ipInput.text.toString().trim())
                true
            } else false
        }

        connectBtn.setOnClickListener { doConnect(ipInput.text.toString().trim()) }
    }

    // ── 焦点链 ────────────────────────────────────────

    private fun rebuildFocusChain() {
        allFocusable.clear()
        // collect history rows
        for (i in 0 until historySection.childCount) {
            val child = historySection.getChildAt(i)
            if (child.isFocusable) allFocusable.add(child)
        }
        // collect discovery rows
        for (i in 0 until discoverySection.childCount) {
            val child = discoverySection.getChildAt(i)
            if (child.isFocusable) allFocusable.add(child)
        }
        // ipInput + connectBtn
        allFocusable.add(ipInput)
        allFocusable.add(connectBtn)

        for (i in allFocusable.indices) {
            val v = allFocusable[i]
            v.nextFocusUpId = if (i > 0) allFocusable[i - 1].id else View.NO_ID
            v.nextFocusDownId = if (i < allFocusable.size - 1) allFocusable[i + 1].id else View.NO_ID
            v.setOnFocusChangeListener { _, hasFocus ->
                if (hasFocus) {
                    scrollView.smoothScrollTo(0, v.top)
                }
            }
        }

        if (allFocusable.isNotEmpty()) {
            allFocusable.first().requestFocus()
        } else {
            ipInput.requestFocus()
        }
    }

    // ── 历史记录 ──────────────────────────────────────

    private fun loadHistory() {
        val raw = getSharedPreferences("owk", Context.MODE_PRIVATE)
            .getString("server_history", "[]") ?: "[]"
        val arr = JSONArray(raw)
        historySection.removeAllViews()
        if (arr.length() == 0) {
            findViewById<TextView>(R.id.historyHeader).isVisible = false
            rebuildFocusChain()
            return
        }
        findViewById<TextView>(R.id.historyHeader).isVisible = true
        for (i in arr.length() - 1 downTo 0) {
            val ip = arr.getString(i)
            historySection.addView(buildHistoryRow(ip))
        }
        rebuildFocusChain()
    }

    private fun buildHistoryRow(ip: String): View {
        val row = LinearLayout(this)
        row.id = View.generateViewId()
        row.layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT, 44
        ).apply { setMargins(0, 0, 0, 4) }
        row.orientation = LinearLayout.HORIZONTAL
        row.gravity = Gravity.CENTER_VERTICAL
        row.setPadding(12, 0, 4, 0)
        row.setBackgroundResource(R.drawable.item_focus_bg)
        row.isFocusable = true
        row.isClickable = true
        row.setOnClickListener { doConnect(ip) }

        val tv = TextView(this)
        tv.text = ip
        tv.textSize = 15f
        tv.setTextColor(Color.WHITE)
        tv.isFocusable = false
        tv.layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
        row.addView(tv)

        row.setOnKeyListener { _, keyCode, event ->
            if (event.action == KeyEvent.ACTION_DOWN && keyCode == KeyEvent.KEYCODE_DPAD_RIGHT) {
                val p = row.parent as? LinearLayout ?: return@setOnKeyListener false
                val idx = p.indexOfChild(row)
                val delBtn = buildDeleteBtn(ip, row, p, idx)
                delBtn.id = View.generateViewId()
                p.addView(delBtn, idx + 1)
                rebuildFocusChain()
                delBtn.requestFocus()
                return@setOnKeyListener true
            }
            false
        }

        return row
    }

    private fun buildDeleteBtn(ip: String, row: View, parent: LinearLayout, idx: Int): View {
        val del = TextView(this).apply {
            text = "✕"
            textSize = 14f
            gravity = Gravity.CENTER
            setTextColor(Color.WHITE)
            layoutParams = LinearLayout.LayoutParams(40, 44).apply {
                setMargins(0, 0, 0, 4)
            }
            setBackgroundResource(R.drawable.item_del_bg)
            isFocusable = true
            isClickable = true
            setOnClickListener {
                deleteHistory(ip)
                parent.removeAllViews()
                loadHistory()
            }
            setOnKeyListener { _, keyCode, _ ->
                if (keyCode == KeyEvent.KEYCODE_DPAD_LEFT) {
                    parent.removeView(this)
                    rebuildFocusChain()
                    row.requestFocus()
                    true
                } else false
            }
        }
        return del
    }

    private fun deleteHistory(ip: String) {
        val sp = getSharedPreferences("owk", Context.MODE_PRIVATE)
        val raw = sp.getString("server_history", "[]") ?: "[]"
        val arr = JSONArray(raw)
        val idx = indexOf(arr, ip)
        if (idx >= 0) arr.remove(idx)
        sp.edit().putString("server_history", arr.toString()).apply()
    }

    // ── 局域网发现 ────────────────────────────────────

    private fun startDiscovery() {
        scanStatus.isVisible = true
        scanStatus.text = "扫描中…"

        CoroutineScope(Dispatchers.Main).launch {
            ServerDiscovery.scanAsync { server ->
                if (!discovered.any { it.ip == server.ip }) {
                    discovered.add(server)
                    discoverySection.addView(buildDiscoveryRow(server))
                    rebuildFocusChain()
                }
            }
            val cnt = discovered.size
            scanStatus.text = if (cnt > 0) "扫描完成: 发现 $cnt 台服务器" else "未发现服务器"
        }
    }

    private fun buildDiscoveryRow(server: DiscoveredServer): View {
        val ip = server.toString()
        val row = TextView(this).apply {
            id = View.generateViewId()
            text = ip
            textSize = 15f
            setTextColor(Color.WHITE)
            gravity = Gravity.CENTER_VERTICAL
            setPadding(12, 0, 12, 0)
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 44
            ).apply { setMargins(0, 0, 0, 4) }
            setBackgroundResource(R.drawable.item_focus_bg)
            isFocusable = true
            isClickable = true
            setOnClickListener { doConnect(ip) }
        }
        return row
    }

    // ── 连接逻辑 ──────────────────────────────────────

    private fun doConnect(input: String) {
        connectBtn.isEnabled = false
        scanStatus.isVisible = true
        scanStatus.text = "连接中…"
        scanStatus.setTextColor(Color.parseColor("#59FFFFFF"))

        val ip: String
        val knownPort: Int?
        if (input.contains(":")) {
            val parts = input.split(":")
            ip = parts[0]
            knownPort = parts[1].toIntOrNull()
        } else {
            ip = input
            knownPort = null
        }

        CoroutineScope(Dispatchers.Main).launch {
            val port = knownPort ?: PortScanner.scan(ip)
            if (port != null) {
                saveHistory(ip)
                startPlayer("http://$ip:$port/player.html")
            } else {
                scanStatus.text = "连接失败"
                scanStatus.setTextColor(Color.parseColor("#FF6666"))
                connectBtn.isEnabled = true
            }
        }
    }

    private fun saveHistory(ip: String) {
        val sp = getSharedPreferences("owk", Context.MODE_PRIVATE)
        val raw = sp.getString("server_history", "[]") ?: "[]"
        val arr = JSONArray(raw)
        val idx = indexOf(arr, ip)
        if (idx >= 0) arr.remove(idx)
        arr.put(ip)
        while (arr.length() > 10) arr.remove(0)
        sp.edit().putString("server_history", arr.toString()).apply()
        findViewById<TextView>(R.id.historyHeader).isVisible = true
        loadHistory()
    }

    private fun indexOf(arr: JSONArray, ip: String): Int {
        for (i in 0 until arr.length()) {
            if (arr.getString(i) == ip) return i
        }
        return -1
    }

    private fun startPlayer(url: String) {
        startActivity(Intent(this, MainActivity::class.java).apply {
            putExtra("url", url)
        })
        finish()
    }
}

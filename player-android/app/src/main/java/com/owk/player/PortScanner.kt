package com.owk.player

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.net.HttpURLConnection
import java.net.InetSocketAddress
import java.net.Socket
import java.net.URL

object PortScanner {

    private val COMMON_PORTS = intArrayOf(8080, 8000, 80, 3000, 5000, 8888, 9090)

    suspend fun scan(ip: String): Int? = withContext(Dispatchers.IO) {
        for (port in COMMON_PORTS) {
            try {
                val s = Socket()
                s.connect(InetSocketAddress(ip, port), 300)
                s.close()
                val url = URL("http://$ip:$port/player.html")
                val conn = url.openConnection() as HttpURLConnection
                conn.connectTimeout = 500
                conn.readTimeout = 500
                if (conn.responseCode in 200..399) {
                    conn.disconnect()
                    return@withContext port
                }
                conn.disconnect()
            } catch (_: Exception) {
            }
        }
        null
    }
}

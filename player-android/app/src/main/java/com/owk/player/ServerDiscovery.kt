package com.owk.player

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.withContext
import java.net.HttpURLConnection
import java.net.NetworkInterface
import java.net.URL

data class DiscoveredServer(val ip: String, val port: Int) {
    override fun toString() = "$ip:$port"
}

object ServerDiscovery {

    private val PORTS = intArrayOf(8080, 8000)
    private const val TIMEOUT = 300
    private const val CONCURRENCY = 30

    suspend fun scanAsync(onFound: (DiscoveredServer) -> Unit) {
        val localIp = getLocalIp() ?: return
        val prefix = localIp.substringBeforeLast('.')
        val targets = mutableListOf<String>()
        for (i in 1..254) targets.add("$prefix.$i")

        coroutineScope {
            targets.chunked(CONCURRENCY).forEach { batch ->
                batch.map { ip ->
                    async(Dispatchers.IO) { checkServer(ip, onFound) }
                }.awaitAll()
            }
        }
    }

    private suspend fun checkServer(ip: String, onFound: (DiscoveredServer) -> Unit) {
        for (port in PORTS) {
            try {
                val url = URL("http://$ip:$port/player.html")
                val conn = url.openConnection() as HttpURLConnection
                conn.connectTimeout = TIMEOUT
                conn.readTimeout = TIMEOUT
                val code = conn.responseCode
                conn.disconnect()
                if (code in 200..399) {
                    val s = DiscoveredServer(ip, port)
                    withContext(Dispatchers.Main) { onFound(s) }
                    return
                }
            } catch (_: Exception) { }
        }
    }

    private fun getLocalIp(): String? {
        try {
            val interfaces = NetworkInterface.getNetworkInterfaces()
            while (interfaces.hasMoreElements()) {
                val ni = interfaces.nextElement()
                if (ni.isLoopback || !ni.isUp) continue
                for (addr in ni.interfaceAddresses) {
                    val ip = addr.address.hostAddress ?: continue
                    if (ip.count { it == '.' } == 3 && !ip.startsWith("127")) {
                        return if (ip.contains('%')) ip.substringBefore('%') else ip
                    }
                }
            }
        } catch (_: Exception) { }
        return null
    }
}

package mg.suricate.tracker

import java.net.HttpURLConnection
import java.net.URL

/** Appels HTTP(S) vers le serveur. À utiliser uniquement hors du thread principal. */
object Net {
    class Result(val code: Int, val body: String)

    fun request(server: String, path: String, method: String, token: String?, json: String?): Result {
        val conn = URL(server.trimEnd('/') + path).openConnection() as HttpURLConnection
        try {
            conn.requestMethod = method
            conn.connectTimeout = 15000
            conn.readTimeout = 20000
            conn.setRequestProperty("Accept", "application/json")
            if (token != null) conn.setRequestProperty("Authorization", "Bearer $token")
            if (json != null) {
                conn.doOutput = true
                conn.setRequestProperty("Content-Type", "application/json")
                conn.outputStream.use { it.write(json.toByteArray(Charsets.UTF_8)) }
            }
            val code = conn.responseCode
            val stream = if (code in 200..299) conn.inputStream else conn.errorStream
            val body = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() } ?: ""
            return Result(code, body)
        } finally {
            conn.disconnect()
        }
    }
}

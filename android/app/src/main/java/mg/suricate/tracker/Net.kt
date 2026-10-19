package mg.suricate.tracker

import java.net.HttpURLConnection
import java.net.URL

/** Appels HTTP(S). À utiliser uniquement hors du thread principal. */
object Net {
    class Result(val code: Int, val body: String)

    private const val UA = "suricate-tracker-android/1.0 (application personnelle)"

    private fun call(
        fullUrl: String, method: String, token: String?, json: String?,
        connectMs: Int, readMs: Int
    ): Result {
        val conn = URL(fullUrl).openConnection() as HttpURLConnection
        try {
            conn.requestMethod = method
            conn.connectTimeout = connectMs
            conn.readTimeout = readMs
            conn.setRequestProperty("Accept", "application/json")
            conn.setRequestProperty("User-Agent", UA)
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

    /** Appel vers le serveur Suricate. */
    fun request(server: String, path: String, method: String, token: String?, json: String?): Result =
        call(server.trimEnd('/') + path, method, token, json, 15000, 20000)

    /** Appel vers un service externe (BeaconDB, OpenStreetMap...) : délais courts pour ne pas bloquer le suivi. */
    fun external(url: String, method: String, json: String?): Result =
        call(url, method, null, json, 6000, 8000)
}

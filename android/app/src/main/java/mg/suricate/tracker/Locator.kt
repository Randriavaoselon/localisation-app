package mg.suricate.tracker

import org.json.JSONArray
import org.json.JSONObject
import java.net.URLEncoder
import java.util.Locale

/** Une position prête à être envoyée. */
class Fix(
    val lat: Double,
    val lon: Double,
    val acc: Double,
    val alt: Double?,
    val altAcc: Double?,
    val method: String,
    var approx: Boolean,
    var place: String = ""
)

/**
 * Mêmes secours que le client Windows (client.py) : position par les routeurs voisins (BeaconDB, puis Mylnikov),
 * puis par l'adresse IP ; nom du quartier / de la ville via OpenStreetMap Nominatim (mis en cache).
 */
object Locator {
    private const val FALLBACK_TTL_MS = 300_000L          // durée de vie du cache des positions de secours
    private val geoCache = HashMap<String, String>()
    private var fallback: Fix? = null
    private var fallbackAt = 0L

    /** À appeler quand le téléphone change de réseau Wi-Fi : l'ancien lieu n'est plus valable. */
    fun clearFallback() {
        fallback = null
        fallbackAt = 0L
    }

    fun fallbackPosition(connected: Ap?, neighbors: List<Ap>, now: Long): Fix? {
        if (fallback == null || now - fallbackAt > FALLBACK_TTL_MS) {
            val found = locateFallback(connected, neighbors)
            fallback = found
            // introuvable : nouvel essai dans 1 minute seulement
            fallbackAt = if (found != null) now else now - FALLBACK_TTL_MS + 60_000L
        }
        val f = fallback ?: return null
        return Fix(f.lat, f.lon, f.acc, null, null, f.method, f.approx)
    }

    private fun locateFallback(connected: Ap?, neighbors: List<Ap>): Fix? =
        locateFromWifi(connected, neighbors) ?: locateWithIp()

    private fun isLocalMac(bssid: String): Boolean =
        try {
            (bssid.substring(0, 2).toInt(16) and 0x02) != 0
        } catch (e: Exception) {
            false
        }

    private fun dist(a: DoubleArray, b: DoubleArray): Double {
        val dlat = (a[0] - b[0]) * 111_320.0
        val dlon = (a[1] - b[1]) * 111_320.0 * Math.cos(Math.toRadians((a[0] + b[0]) / 2))
        return Math.hypot(dlat, dlon)
    }

    private fun median(v: List<Double>): Double {
        val s = v.sorted()
        val n = s.size
        return if (n % 2 == 1) s[n / 2] else (s[n / 2 - 1] + s[n / 2]) / 2.0
    }

    private fun locateFromWifi(connected: Ap?, neighbors: List<Ap>): Fix? {
        val all = ArrayList<Ap>()
        if (connected != null) all.add(connected)
        all.addAll(neighbors)                            // déjà triés par signal décroissant
        val usable = all.filter { !isLocalMac(it.bssid) }.take(10)
        if (usable.isEmpty()) return null
        return beacondb(usable) ?: mylnikov(usable)
    }

    private fun beacondb(aps: List<Ap>): Fix? {
        if (aps.size < 2) return null
        return try {
            val arr = JSONArray()
            for (a in aps) arr.put(JSONObject().put("macAddress", a.bssid).put("signalStrength", a.dbm))
            val body = JSONObject().put("considerIp", false).put("wifiAccessPoints", arr).toString()
            val r = Net.external("https://api.beacondb.net/v1/geolocate", "POST", body)
            if (r.code != 200) {
                null
            } else {
                val j = JSONObject(r.body)
                val loc = j.getJSONObject("location")
                val acc = j.optDouble("accuracy", 300.0)
                Fix(
                    loc.getDouble("lat"), loc.getDouble("lng"), if (acc.isNaN() || acc <= 0.0) 300.0 else acc,
                    null, null, "Wi-Fi voisins via BeaconDB (" + aps.size + " routeurs)", false
                )
            }
        } catch (e: Exception) {
            null
        }
    }

    private fun mylnikov(aps: List<Ap>): Fix? {
        val pts = ArrayList<DoubleArray>()
        val deadline = System.currentTimeMillis() + 20_000L
        for (a in aps.take(6)) {
            if (System.currentTimeMillis() > deadline) break
            try {
                val url = "https://api.mylnikov.org/geolocation/wifi?v=1.1&data=open&bssid=" +
                    URLEncoder.encode(a.bssid.uppercase(Locale.ROOT), "UTF-8")
                val r = Net.external(url, "GET", null)
                if (r.code != 200) continue
                val d = JSONObject(r.body).optJSONObject("data") ?: continue
                if (d.has("lat") && d.has("lon")) pts.add(doubleArrayOf(d.getDouble("lat"), d.getDouble("lon")))
            } catch (e: Exception) {
                // routeur inconnu ou service injoignable : on passe au suivant
            }
        }
        if (pts.isEmpty()) return null
        val med = doubleArrayOf(median(pts.map { it[0] }), median(pts.map { it[1] }))
        var good = pts.filter { dist(it, med) < 5000.0 }
        if (good.isEmpty()) good = pts
        val center = doubleArrayOf(median(good.map { it[0] }), median(good.map { it[1] }))
        val radius = good.maxOf { dist(it, center) }
        return Fix(
            center[0], center[1], maxOf(150.0, radius), null, null,
            "Wi-Fi voisins (" + good.size + "/" + aps.size + " routeurs trouvés)", false
        )
    }

    private fun locateWithIp(): Fix? =
        try {
            val r = Net.external("https://ipwho.is/", "GET", null)
            val j = JSONObject(r.body)
            if (!j.optBoolean("success", true)) {
                null
            } else {
                Fix(
                    j.getDouble("latitude"), j.getDouble("longitude"), 5000.0, null, null,
                    "IP publique (" + j.optString("city", "?") + ") - approximative", true
                )
            }
        } catch (e: Exception) {
            null
        }

    /** Quartier / ville via OpenStreetMap Nominatim (mis en cache), comme le client Windows. */
    fun reverseGeocode(lat: Double, lon: Double): String {
        val key = String.format(Locale.US, "%.3f,%.3f", lat, lon)
        val cached = geoCache[key]
        if (cached != null) return cached
        return try {
            val url = "https://nominatim.openstreetmap.org/reverse?format=jsonv2" +
                "&lat=" + String.format(Locale.US, "%.6f", lat) +
                "&lon=" + String.format(Locale.US, "%.6f", lon) +
                "&zoom=16&addressdetails=1&accept-language=fr"
            val r = Net.external(url, "GET", null)
            val j = JSONObject(r.body)
            val a = j.optJSONObject("address")
            val area = firstOf(a, arrayOf("neighbourhood", "quarter", "suburb", "city_district", "hamlet"))
            val city = firstOf(a, arrayOf("city", "town", "village", "municipality", "county"))
            var place = listOfNotNull(area, city).filter { it.isNotEmpty() }.joinToString(", ")
            if (place.isEmpty()) place = j.optString("display_name", "")
            if (geoCache.size > 500) geoCache.clear()
            geoCache[key] = place
            place
        } catch (e: Exception) {
            ""
        }
    }

    private fun firstOf(a: JSONObject?, keys: Array<String>): String? {
        if (a == null) return null
        for (k in keys) if (a.has(k)) return a.optString(k)
        return null
    }
}

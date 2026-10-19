package mg.suricate.tracker

import android.content.Context
import android.net.wifi.ScanResult
import android.net.wifi.WifiManager
import java.util.Locale

/** Un routeur Wi-Fi : nom, adresse MAC (BSSID) et puissance du signal en dBm. */
class Ap(val ssid: String, val bssid: String, val dbm: Int)

object Wifi {
    private const val FAKE_BSSID = "02:00:00:00:00:00"      // valeur renvoyée par Android quand l'accès est refusé

    /**
     * Wi-Fi auquel le téléphone est connecté + routeurs voisins (dernier balayage connu).
     * Android ne donne le nom du Wi-Fi et la liste des voisins que si la localisation est autorisée et allumée.
     */
    @Suppress("DEPRECATION")
    fun read(ctx: Context): Pair<Ap?, List<Ap>> {
        val wm = ctx.applicationContext.getSystemService(Context.WIFI_SERVICE) as? WifiManager
            ?: return Pair(null, emptyList())
        var connected: Ap? = null
        try {
            if (wm.isWifiEnabled) {
                val info = wm.connectionInfo
                val bssid = info?.bssid
                if (info != null && bssid != null && bssid != FAKE_BSSID) {
                    var ssid = (info.ssid ?: "").trim('"')
                    if (ssid == "<unknown ssid>") ssid = ""
                    val rssi = info.rssi
                    connected = Ap(ssid, bssid.lowercase(Locale.ROOT), if (rssi in -127..0) rssi else -70)
                }
            }
        } catch (e: SecurityException) {
            // accès refusé : pas de Wi-Fi détecté
        }
        try {
            wm.startScan()          // demande un nouveau balayage (limité par Android) ; lu au cycle suivant
        } catch (e: Exception) {
            // ignoré
        }
        val list = ArrayList<Ap>()
        try {
            val results: List<ScanResult> = wm.scanResults ?: emptyList()
            for (r in results) {
                val b = r.BSSID?.lowercase(Locale.ROOT) ?: continue
                if (b == connected?.bssid) continue
                list.add(Ap(r.SSID ?: "", b, r.level))
            }
        } catch (e: SecurityException) {
            // accès refusé : pas de voisins
        }
        list.sortByDescending { it.dbm }
        return Pair(connected, list)
    }
}

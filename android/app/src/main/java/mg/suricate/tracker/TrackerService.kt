package mg.suricate.tracker

import android.annotation.SuppressLint
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.os.Build
import android.os.Bundle
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import org.json.JSONObject
import java.io.IOException
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.TimeUnit

/**
 * Service de premier plan (notification permanente) : interroge le serveur toutes les ~10 s
 * (« suivre » ou « attendre ») et, quand le suivi est actif, envoie la position toutes les ~30 s.
 * Même protocole que client.py : /api/agent/poll et /api/agent/report, jeton dans « Authorization: Bearer ».
 */
class TrackerService : Service() {

    companion object {
        private const val CHANNEL = "suivi"
        private const val NOTIF_ID = 1

        fun start(context: Context) {
            try {
                val i = Intent(context, TrackerService::class.java)
                if (Build.VERSION.SDK_INT >= 26) context.startForegroundService(i) else context.startService(i)
            } catch (e: Exception) {
                Prefs.putStatus(context) { it.putString("err", "Démarrage du service refusé : " + (e.message ?: "")) }
            }
        }
    }

    private lateinit var lm: LocationManager
    private var worker: ScheduledExecutorService? = null
    @Volatile private var lastFix: Location? = null
    @Volatile private var providersChanged = false
    private var desired = "stopped"
    private var interval = 30
    private var pollEvery = 10
    private var tracking = false
    private var lastPoll = 0L
    private var lastSent = 0L
    private var sentCount = 0
    private var wake: PowerManager.WakeLock? = null

    private val listener = object : LocationListener {
        override fun onLocationChanged(location: Location) {
            val last = lastFix
            if (last == null || location.accuracy <= last.accuracy || location.time - last.time > 20_000L) {
                lastFix = location
            }
        }

        override fun onProviderEnabled(provider: String) {
            providersChanged = true
        }

        override fun onProviderDisabled(provider: String) {
            providersChanged = true
        }

        @Deprecated("Obsolète depuis l'API 29, mais obligatoire avant")
        override fun onStatusChanged(provider: String?, status: Int, extras: Bundle?) {
        }
    }

    override fun onCreate() {
        super.onCreate()
        lm = getSystemService(Context.LOCATION_SERVICE) as LocationManager
        if (Build.VERSION.SDK_INT >= 26) {          // les canaux n'existent qu'à partir d'Android 8
            val channel = NotificationChannel(CHANNEL, "Suivi de position", NotificationManager.IMPORTANCE_LOW)
            getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
        }
        sentCount = Prefs.getInt(this, "count")
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // Android impose d'appeler startForeground() dans les 5 s qui suivent le démarrage du service.
        if (!startAsForeground("Démarrage…")) {
            stopSelf()
            return START_NOT_STICKY
        }
        if (!Prefs.isEnrolled(this)) {
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
            return START_NOT_STICKY
        }
        if (worker == null) {
            val w = Executors.newSingleThreadScheduledExecutor()
            worker = w
            w.scheduleWithFixedDelay({ cycle() }, 0L, 5L, TimeUnit.SECONDS)
        }
        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        worker?.shutdownNow()
        unregister()
        releaseWake()
        super.onDestroy()
    }

    // ------------------------------------------------------------------ boucle principale
    private fun cycle() {
        try {
            if (!Prefs.isEnrolled(this)) {
                shutdown()
                return
            }
            val now = System.currentTimeMillis()
            if (now - lastPoll >= pollEvery * 1000L - 500L) {
                lastPoll = now
                poll()
            }
            if (!Prefs.isEnrolled(this)) {      // jeton refusé pendant le sondage
                return
            }
            if (desired == "running") {
                setTracking(true)
                if (tracking && now - lastSent >= interval * 1000L - 1000L) sendFix(now)
            } else {
                setTracking(false)
            }
            refreshStatus()
        } catch (e: Throwable) {
            Prefs.putStatus(this) { it.putString("err", e.message ?: e.toString()) }
        }
    }

    private fun poll() {
        try {
            val r = Net.request(Prefs.server(this), "/api/agent/poll", "GET", Prefs.token(this), null)
            if (r.code == 401) {
                onInvalid()
            } else if (r.code in 200..299) {
                val j = JSONObject(r.body)
                desired = j.optString("desired", "stopped")
                interval = maxOf(10, j.optInt("interval", 30))
                pollEvery = maxOf(5, j.optInt("poll", 10))
                val n = j.optString("name", "")
                if (n.isNotEmpty() && n != Prefs.name(this)) Prefs.setName(this, n)
                Prefs.putStatus(this) { it.putString("net", "") }
            } else {
                Prefs.putStatus(this) { it.putString("net", "Serveur : erreur " + r.code) }
            }
        } catch (e: IOException) {
            Prefs.putStatus(this) { it.putString("net", "Serveur injoignable : nouvel essai automatique") }
        }
    }

    private fun sendFix(now: Long) {
        val fix = lastFix
        if (fix == null || now - fix.time > 120_000L) {
            Prefs.putStatus(this) { it.putString("err", "En attente d'un signal GPS…") }
            return
        }
        val pos = JSONObject()
            .put("lat", fix.latitude)
            .put("lon", fix.longitude)
            .put("accuracy_m", fix.accuracy.toDouble())
            .put("method", "Android · " + (if (fix.provider == LocationManager.GPS_PROVIDER) "GPS" else "réseau"))
            .put("approx", fix.accuracy > 2000f)
        if (fix.hasAltitude()) pos.put("altitude", fix.altitude)
        if (Build.VERSION.SDK_INT >= 26 && fix.hasVerticalAccuracy()) pos.put("altitude_accuracy", fix.verticalAccuracyMeters.toDouble())
        val fmt = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.US)
        fmt.timeZone = TimeZone.getTimeZone("UTC")
        val body = JSONObject().put("position", pos).put("client_time", fmt.format(Date(fix.time)))
        try {
            val r = Net.request(Prefs.server(this), "/api/agent/report", "POST", Prefs.token(this), body.toString())
            if (r.code == 401) {
                onInvalid()
                return
            }
            if (r.code !in 200..299) {
                Prefs.putStatus(this) { it.putString("err", "Envoi refusé par le serveur (" + r.code + ")") }
                return
            }
            val j = JSONObject(r.body)
            lastSent = now
            if (j.optBoolean("stored")) sentCount++
            val d = j.optString("desired", "running")
            if (d != "running") desired = d
            val count = sentCount
            Prefs.putStatus(this) {
                it.putString("err", "").putString("net", "").putLong("sentAt", now).putInt("count", count)
            }
        } catch (e: IOException) {
            Prefs.putStatus(this) { it.putString("net", "Serveur injoignable : nouvel essai automatique") }
        }
    }

    private fun onInvalid() {
        Prefs.clear(this)
        Prefs.putStatus(this) { it.putString("err", "Ce client n'est plus valide : réactivez-le avec un nouveau code.") }
        shutdown()
    }

    private fun shutdown() {
        setTracking(false)
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    // ------------------------------------------------------------------ position
    private fun hasLocationPermission(): Boolean =
        checkSelfPermission(android.Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED ||
            checkSelfPermission(android.Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED

    @SuppressLint("MissingPermission")
    private fun setTracking(on: Boolean) {
        if (on) {
            if (tracking && !providersChanged) {
                renewWake()
                return
            }
            if (tracking) unregister()
            providersChanged = false
            if (!hasLocationPermission()) {
                Prefs.putStatus(this) { it.putString("err", "Autorisation de localisation manquante : ouvrez l'application.") }
                tracking = false
                return
            }
            try {
                if (lm.isProviderEnabled(LocationManager.GPS_PROVIDER)) {
                    lm.requestLocationUpdates(LocationManager.GPS_PROVIDER, 5000L, 0f, listener, Looper.getMainLooper())
                }
                if (lm.isProviderEnabled(LocationManager.NETWORK_PROVIDER)) {
                    lm.requestLocationUpdates(LocationManager.NETWORK_PROVIDER, 10000L, 0f, listener, Looper.getMainLooper())
                }
                tracking = true
                renewWake()
            } catch (e: SecurityException) {
                Prefs.putStatus(this) { it.putString("err", "Localisation refusée par Android : " + (e.message ?: "")) }
                tracking = false
            }
        } else if (tracking) {
            unregister()
            tracking = false
            releaseWake()
        }
    }

    private fun unregister() {
        try {
            lm.removeUpdates(listener)
        } catch (e: Exception) {
            // rien à faire
        }
    }

    private fun renewWake() {
        val w = wake ?: (getSystemService(Context.POWER_SERVICE) as PowerManager)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "suricate:suivi").also {
                it.setReferenceCounted(false)
                wake = it
            }
        w.acquire(15 * 60 * 1000L)      // renouvelé à chaque cycle tant que le suivi est actif
    }

    private fun releaseWake() {
        try {
            val w = wake
            if (w != null && w.isHeld) w.release()
        } catch (e: Exception) {
            // rien à faire
        }
    }

    // ------------------------------------------------------------------ notification et état affiché
    private fun refreshStatus() {
        val gps = lm.isProviderEnabled(LocationManager.GPS_PROVIDER)
        val locOn = if (Build.VERSION.SDK_INT >= 28) {
            lm.isLocationEnabled
        } else {
            gps || lm.isProviderEnabled(LocationManager.NETWORK_PROVIDER)
        }
        val fix = lastFix
        val d = desired
        Prefs.putStatus(this) {
            it.putString("desired", d).putBoolean("gps", gps)
            if (fix != null) {
                it.putString("lat", String.format(Locale.US, "%.5f", fix.latitude))
                it.putString("lon", String.format(Locale.US, "%.5f", fix.longitude))
                it.putInt("acc", Math.round(fix.accuracy))
            }
        }
        val text = when {
            !locOn -> "⚠️ Localisation désactivée : touchez pour l'activer"
            d == "running" && !gps -> "Suivi actif (position réseau, peu précise) · choisissez « Haute précision »"
            d == "running" -> "Suivi actif · envoi toutes les $interval s"
            else -> "En attente : suivi arrêté depuis le tableau de bord"
        }
        getSystemService(NotificationManager::class.java).notify(NOTIF_ID, buildNotification(text))
    }

    @Suppress("DEPRECATION")
    private fun buildNotification(text: String): Notification {
        val pi = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        val builder = if (Build.VERSION.SDK_INT >= 26) {
            Notification.Builder(this, CHANNEL)
        } else {
            Notification.Builder(this).setPriority(Notification.PRIORITY_LOW)
        }
        return builder
            .setContentTitle("Suricate Tracker")
            .setContentText(text)
            .setSmallIcon(R.drawable.ic_stat)
            .setContentIntent(pi)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .build()
    }

    private fun startAsForeground(text: String): Boolean {
        return try {
            val n = buildNotification(text)
            if (Build.VERSION.SDK_INT >= 29) {
                startForeground(NOTIF_ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION)
            } else {
                startForeground(NOTIF_ID, n)
            }
            true
        } catch (e: Exception) {
            Prefs.putStatus(this) { it.putString("err", "Service de suivi refusé par Android : " + (e.message ?: "")) }
            false
        }
    }
}

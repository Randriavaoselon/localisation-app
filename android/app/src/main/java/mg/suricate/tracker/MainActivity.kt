package mg.suricate.tracker

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Typeface
import android.location.LocationManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.provider.Settings
import android.text.InputType
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import org.json.JSONObject
import java.util.Locale

/**
 * Écran unique : activation du client (adresse du serveur + code), puis vérification guidée des réglages
 * (position précise, « Toujours autoriser », GPS activé, batterie). Le suivi lui-même tourne dans TrackerService.
 */
class MainActivity : Activity() {

    private val ui = Handler(Looper.getMainLooper())
    private var dialog: AlertDialog? = null
    private var showEnroll = false

    private lateinit var enrollBox: LinearLayout
    private lateinit var mainBox: LinearLayout
    private lateinit var serverInput: EditText
    private lateinit var codeInput: EditText
    private lateinit var enrollBtn: Button
    private lateinit var enrollMsg: TextView
    private lateinit var statusTitle: TextView
    private lateinit var statusText: TextView

    private val ticker = object : Runnable {
        override fun run() {
            refresh()
            ui.postDelayed(this, 1000L)
        }
    }

    // ------------------------------------------------------------------ construction de l'écran
    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).toInt()

    private fun label(text: String, size: Float, bold: Boolean = false): TextView {
        val t = TextView(this)
        t.text = text
        t.textSize = size
        if (bold) t.setTypeface(t.typeface, Typeface.BOLD)
        t.setPadding(0, dp(6), 0, dp(6))
        return t
    }

    private fun button(text: String, onClick: () -> Unit): Button {
        val b = Button(this)
        b.text = text
        b.setOnClickListener { onClick() }
        return b
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val root = LinearLayout(this)
        root.orientation = LinearLayout.VERTICAL
        root.setPadding(dp(20), dp(20), dp(20), dp(20))
        root.addView(label("Service système", 24f, true))

        enrollBox = LinearLayout(this)
        enrollBox.orientation = LinearLayout.VERTICAL
        enrollBox.addView(label("Activer ce téléphone", 18f, true))
        enrollBox.addView(
            label(
                "Sur le tableau de bord, créez un client pour ce téléphone, cliquez sur « 📱 Android », " +
                    "puis saisissez ici le code d'activation.", 14f
            )
        )
        serverInput = EditText(this)
        serverInput.hint = "Adresse du serveur (https://…)"
        serverInput.inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
        serverInput.setText(BuildConfig.DEFAULT_SERVER)
        codeInput = EditText(this)
        codeInput.hint = "XXXX-XXXX-XXXX"
        codeInput.inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_CAP_CHARACTERS or
            InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
        enrollBtn = button("Activer") { doEnroll() }
        enrollMsg = label("", 14f)
        enrollBox.addView(serverInput)
        enrollBox.addView(codeInput)
        enrollBox.addView(enrollBtn)
        enrollBox.addView(enrollMsg)

        mainBox = LinearLayout(this)
        mainBox.orientation = LinearLayout.VERTICAL
        statusTitle = label("", 20f, true)
        statusText = label("", 15f)
        mainBox.addView(statusTitle)
        mainBox.addView(statusText)
        mainBox.addView(button("Vérifier les réglages (GPS, autorisations)") { recheckSetup() })
        mainBox.addView(button("Oublier ce client sur ce téléphone") { confirmForget() })

        root.addView(enrollBox)
        root.addView(mainBox)
        val scroll = ScrollView(this)
        scroll.addView(root)
        setContentView(scroll)
        handleIntent(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleIntent(intent)
    }

    /** Lien suricate://enroll?server=…&code=… : pré-remplit seulement, l'utilisateur doit toucher « Activer ». */
    private fun handleIntent(i: Intent?) {
        val uri = i?.data ?: return
        if (uri.scheme != "suricate") return
        val s = uri.getQueryParameter("server")
        val c = uri.getQueryParameter("code")
        if (!s.isNullOrEmpty()) serverInput.setText(s)
        if (!c.isNullOrEmpty()) codeInput.setText(c)
        showEnroll = true
        enrollMsg.text = "Vérifiez l'adresse du serveur, puis touchez « Activer »."
    }

    override fun onResume() {
        super.onResume()
        ui.removeCallbacks(ticker)
        ui.post(ticker)
        ensureSetup()
    }

    override fun onPause() {
        super.onPause()
        ui.removeCallbacks(ticker)
        dialog?.dismiss()
        dialog = null
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        ensureSetup()
    }

    // ------------------------------------------------------------------ activation
    private fun doEnroll() {
        var server = serverInput.text.toString().trim().trimEnd('/')
        val code = codeInput.text.toString().trim().uppercase(Locale.ROOT)
        if (server.isNotEmpty() && !server.contains("://")) server = "https://$server"
        if (!server.startsWith("https://")) {
            enrollMsg.text = "⚠️ L'adresse du serveur doit commencer par https://"
            return
        }
        if (code.isEmpty()) {
            enrollMsg.text = "⚠️ Saisissez le code d'activation."
            return
        }
        enrollBtn.isEnabled = false
        enrollMsg.text = "Activation…"
        val ctx = applicationContext
        Thread {
            var msg = ""
            try {
                val r = Net.request(server, "/api/agent/enroll", "POST", null, JSONObject().put("code", code).toString())
                val j = JSONObject(if (r.body.isEmpty()) "{}" else r.body)
                if (r.code in 200..299 && j.optBoolean("ok")) {
                    Prefs.save(ctx, server, j.getString("token"), j.optString("name", ""))
                } else {
                    msg = "⚠️ " + j.optString("error", "erreur " + r.code)
                }
            } catch (e: Exception) {
                msg = "⚠️ " + (e.message ?: "erreur réseau")
            }
            runOnUiThread {
                enrollBtn.isEnabled = true
                if (msg.isEmpty()) {
                    showEnroll = false
                    enrollMsg.text = ""
                    codeInput.setText("")
                    refresh()
                    ensureSetup()
                } else {
                    enrollMsg.text = msg
                }
            }
        }.start()
    }

    private fun confirmForget() {
        AlertDialog.Builder(this)
            .setTitle("Oublier ce client ?")
            .setMessage("Le suivi s'arrête sur ce téléphone. Il faudra un nouveau code pour le réactiver.")
            .setPositiveButton("Oublier") { _, _ ->
                stopService(Intent(this, TrackerService::class.java))
                Prefs.clear(this)
                refresh()
            }
            .setNegativeButton("Annuler", null)
            .show()
    }

    // ------------------------------------------------------------------ réglages obligatoires, étape par étape
    private fun has(p: String): Boolean = checkSelfPermission(p) == PackageManager.PERMISSION_GRANTED

    private fun gpsEnabled(): Boolean {
        val lm = getSystemService(Context.LOCATION_SERVICE) as LocationManager
        return lm.isProviderEnabled(LocationManager.GPS_PROVIDER)
    }

    /** Localisation allumée dans les réglages, quel que soit le mode (haute précision, économie de batterie, GPS seul). */
    private fun locationEnabled(): Boolean {
        val lm = getSystemService(Context.LOCATION_SERVICE) as LocationManager
        return if (Build.VERSION.SDK_INT >= 28) {
            lm.isLocationEnabled
        } else {
            lm.isProviderEnabled(LocationManager.GPS_PROVIDER) || lm.isProviderEnabled(LocationManager.NETWORK_PROVIDER)
        }
    }

    private fun flag(key: String) {
        Prefs.putStatus(this) { it.putBoolean(key, true) }
    }

    private fun openAppSettings() {
        startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:$packageName")))
    }

    private fun ask(title: String, msg: String, ok: String, action: () -> Unit) {
        dialog?.dismiss()
        dialog = AlertDialog.Builder(this)
            .setTitle(title)
            .setMessage(msg)
            .setCancelable(false)
            .setPositiveButton(ok) { _, _ -> action() }
            .show()
    }

    private fun recheckSetup() {
        Prefs.putStatus(this) {
            it.putBoolean("askedNotif", false).putBoolean("askedBg", false).putBoolean("askedBatt", false)
        }
        ensureSetup()
    }

    /**
     * Android interdit d'activer le GPS en silence : l'application bloque donc l'écran sur une fenêtre
     * non fermable tant que le GPS est éteint, et envoie l'utilisateur directement aux réglages.
     */
    private fun ensureSetup() {
        if (!Prefs.isEnrolled(this) || isFinishing) return
        dialog?.dismiss()
        dialog = null

        // 1. position précise (obligatoire)
        if (!has(Manifest.permission.ACCESS_FINE_LOCATION)) {
            val asked = Prefs.getBool(this, "askedLoc")
            ask(
                "Autoriser la position",
                "Suricate a besoin de la position précise de ce téléphone. À la question suivante, " +
                    "choisissez « Autoriser » (ou « Précise » puis « Pendant l'utilisation de l'application » sur les Android récents).",
                "Continuer"
            ) {
                flag("askedLoc")
                if (asked && !shouldShowRequestPermissionRationale(Manifest.permission.ACCESS_FINE_LOCATION)) {
                    openAppSettings()
                } else {
                    requestPermissions(
                        arrayOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION), 1
                    )
                }
            }
            return
        }

        // 2. notifications (Android 13 et +) : demandées une seule fois
        if (Build.VERSION.SDK_INT >= 33 && !has(Manifest.permission.POST_NOTIFICATIONS) &&
            !Prefs.getBool(this, "askedNotif")
        ) {
            ask(
                "Autoriser les notifications",
                "Android exige une notification visible tant que le suivi est actif. Acceptez-la à la question suivante.",
                "Continuer"
            ) {
                flag("askedNotif")
                requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 2)
            }
            return
        }

        // 3. position « Toujours autoriser » (Android 10 et +) : indispensable pour le démarrage automatique
        if (Build.VERSION.SDK_INT >= 29 && !has(Manifest.permission.ACCESS_BACKGROUND_LOCATION) &&
            !Prefs.getBool(this, "askedBg")
        ) {
            ask(
                "Position « Toujours autoriser »",
                "Pour suivre ce téléphone écran éteint et après chaque redémarrage, choisissez « Toujours autoriser » " +
                    "(Autorisations > Position) dans l'écran suivant.",
                "Continuer"
            ) {
                flag("askedBg")
                if (Build.VERSION.SDK_INT >= 30) {
                    openAppSettings()
                } else {
                    requestPermissions(arrayOf(Manifest.permission.ACCESS_BACKGROUND_LOCATION), 3)
                }
            }
            return
        }

        // 4. localisation activée : redemandée à chaque ouverture tant qu'elle est éteinte
        if (!locationEnabled()) {
            ask(
                "Activer la localisation",
                "La localisation (GPS) de ce téléphone est désactivée. Activez-la, de préférence en mode " +
                    "« Haute précision », puis revenez à l'application : le suivi ne peut pas fonctionner sans elle.",
                "Ouvrir les réglages"
            ) {
                try {
                    startActivity(Intent(Settings.ACTION_LOCATION_SOURCE_SETTINGS))
                } catch (e: Exception) {
                    startActivity(Intent(Settings.ACTION_SETTINGS))
                }
            }
            return
        }

        // 5. batterie : demandée une seule fois
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        if (!pm.isIgnoringBatteryOptimizations(packageName) && !Prefs.getBool(this, "askedBatt")) {
            ask(
                "Ne pas couper le suivi",
                "Pour que l'économie d'énergie ne coupe pas le suivi, autorisez Suricate à ignorer l'optimisation " +
                    "de la batterie à la question suivante.",
                "Continuer"
            ) {
                flag("askedBatt")
                try {
                    startActivity(
                        Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:$packageName"))
                    )
                } catch (e: Exception) {
                    startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
                }
            }
            return
        }

        // tout est en place : le service suit les consignes du tableau de bord et repartira seul au démarrage
        TrackerService.start(this)
    }

    // ------------------------------------------------------------------ affichage
    private fun ago(t: Long): String {
        val s = (System.currentTimeMillis() - t) / 1000L
        return if (s < 60L) "il y a $s s" else "il y a ${s / 60L} min"
    }

    private fun refresh() {
        val enrolled = Prefs.isEnrolled(this)
        enrollBox.visibility = if (!enrolled || showEnroll) View.VISIBLE else View.GONE
        mainBox.visibility = if (enrolled) View.VISIBLE else View.GONE
        if (!enrolled) {
            val e = Prefs.getString(this, "err")
            if (e.isNotEmpty() && enrollMsg.text.isEmpty()) enrollMsg.text = e
            return
        }
        val desired = Prefs.getString(this, "desired")
        val net = Prefs.getString(this, "net")
        val locOn = locationEnabled()
        statusTitle.text = when {
            !locOn -> "⚠️ Localisation désactivée"
            net.isNotEmpty() -> "🔴 Serveur injoignable"
            desired == "running" -> "🟢 Suivi actif"
            else -> "⏸ Suivi arrêté (en attente de ▶ Démarrer)"
        }
        val lat = Prefs.getString(this, "lat")
        val lon = Prefs.getString(this, "lon")
        val acc = Prefs.getInt(this, "acc")
        val sentAt = Prefs.getLong(this, "sentAt")
        val sb = StringBuilder()
        sb.append("Client : ").append(Prefs.name(this)).append('\n')
        sb.append("Serveur : ").append(Prefs.server(this)).append('\n')
        sb.append("Dernière position : ")
            .append(if (lat.isEmpty()) "–" else "$lat, $lon (±$acc m)").append('\n')
        val ssid = Prefs.getString(this, "ssid")
        val place = Prefs.getString(this, "place")
        val method = Prefs.getString(this, "method")
        sb.append("Wi-Fi : ").append(if (ssid.isEmpty()) "–" else ssid).append('\n')
        sb.append("Lieu : ").append(if (place.isEmpty()) "–" else place).append('\n')
        sb.append("Méthode : ").append(if (method.isEmpty()) "–" else method).append('\n')
        sb.append("Dernier envoi : ").append(if (sentAt == 0L) "–" else ago(sentAt)).append('\n')
        sb.append("Positions envoyées : ").append(Prefs.getInt(this, "count")).append('\n')
        val warn = ArrayList<String>()
        if (locOn && !gpsEnabled()) {
            warn.add("Le GPS est coupé (mode « Économie de batterie » ?) : les positions viennent du réseau et sont peu précises. Choisissez « Haute précision » dans les réglages de localisation.")
        }
        if (Build.VERSION.SDK_INT >= 29 && !has(Manifest.permission.ACCESS_BACKGROUND_LOCATION)) {
            warn.add("Position « Toujours autoriser » non accordée : le suivi écran éteint et le démarrage automatique peuvent échouer.")
        }
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        if (!pm.isIgnoringBatteryOptimizations(packageName)) {
            warn.add("L'optimisation de la batterie peut couper le suivi.")
        }
        val err = Prefs.getString(this, "err")
        if (err.isNotEmpty()) warn.add(err)
        if (net.isNotEmpty()) warn.add(net)
        for (w in warn) sb.append("\n⚠️ ").append(w)
        statusText.text = sb.toString()
    }
}

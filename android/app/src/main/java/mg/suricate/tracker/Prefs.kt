package mg.suricate.tracker

import android.content.Context
import android.content.SharedPreferences

/** Réglages enregistrés sur le téléphone (adresse du serveur, jeton) et état affiché à l'écran. */
object Prefs {
    private fun sp(c: Context): SharedPreferences = c.getSharedPreferences("suricate", Context.MODE_PRIVATE)

    fun server(c: Context): String = sp(c).getString("server", "") ?: ""
    fun token(c: Context): String = sp(c).getString("token", "") ?: ""
    fun name(c: Context): String = sp(c).getString("name", "") ?: ""
    fun isEnrolled(c: Context): Boolean = server(c).isNotEmpty() && token(c).isNotEmpty()

    fun save(c: Context, server: String, token: String, name: String) {
        sp(c).edit().clear().putString("server", server).putString("token", token).putString("name", name).apply()
    }

    fun setName(c: Context, name: String) {
        sp(c).edit().putString("name", name).apply()
    }

    fun clear(c: Context) {
        sp(c).edit().clear().apply()
    }

    fun getString(c: Context, key: String): String = sp(c).getString(key, "") ?: ""
    fun getLong(c: Context, key: String): Long = sp(c).getLong(key, 0L)
    fun getInt(c: Context, key: String): Int = sp(c).getInt(key, 0)
    fun getBool(c: Context, key: String): Boolean = sp(c).getBoolean(key, false)

    fun putStatus(c: Context, f: (SharedPreferences.Editor) -> Unit) {
        val e = sp(c).edit()
        f(e)
        e.apply()
    }
}

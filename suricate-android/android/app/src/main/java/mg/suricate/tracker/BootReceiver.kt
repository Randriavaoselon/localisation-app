package mg.suricate.tracker

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** Relance le suivi à chaque démarrage du téléphone (et après une mise à jour de l'application). */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (Prefs.isEnrolled(context)) TrackerService.start(context)
    }
}

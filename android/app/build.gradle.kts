plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Adresse du serveur proposée par défaut dans l'application : gradle -PserverUrl=https://mon-serveur
val serverUrl: String = (project.findProperty("serverUrl") as String?) ?: ""

android {
    namespace = "mg.suricate.tracker"
    compileSdk = 34

    defaultConfig {
        applicationId = "mg.suricate.tracker"
        minSdk = 24          // Android 7.0 minimum
        targetSdk = 34
        versionCode = (project.findProperty("versionCode") as String?)?.toIntOrNull() ?: 1
        versionName = "1.0"
        buildConfigField("String", "DEFAULT_SERVER", "\"${serverUrl}\"")
    }

    buildFeatures {
        buildConfig = true
    }

    // Clé de signature unique (créée une fois par le workflow GitHub et conservée dans le dépôt) :
    // une même clé permet de mettre l'application à jour sans la désinstaller.
    signingConfigs {
        create("suricate") {
            val ks = rootProject.file("suricate.keystore")
            if (ks.exists()) {
                storeFile = ks
                storeType = "pkcs12"
                storePassword = "suricate-tracker"
                keyAlias = "suricate"
                keyPassword = "suricate-tracker"
                enableV1Signing = true   // signature compatible avec tous les Android 7+
                enableV2Signing = true
            }
        }
    }

    buildTypes {
        getByName("release") {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("suricate")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    lint {
        abortOnError = false
        checkReleaseBuilds = false
    }
}

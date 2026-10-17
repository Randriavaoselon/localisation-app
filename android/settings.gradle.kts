pluginManagement {
    repositories {
        google()
        // Miroir Google de Maven Central : utilisé avant mavenCentral() car celui-ci renvoie 403 sur le runner
        maven("https://maven-central.storage-download.googleapis.com/maven2/")
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        maven("https://maven-central.storage-download.googleapis.com/maven2/")
        mavenCentral()
    }
}
rootProject.name = "Suricate"
include(":app")
plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

// The Python package (engines, risk, execution, API, dashboard) is copied from ../../quanttitan
// at configuration time, so the APK always embeds exactly the code that the test suite checked.
val pyStage = layout.buildDirectory.dir("qtpython").get().asFile
project.delete(pyStage)
project.copy {
    from("../../quanttitan") { exclude("**/__pycache__/**", "**/*.pyc") }
    into(File(pyStage, "quanttitan"))
}

android {
    namespace = "ai.quanttitan.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "ai.quanttitan.app"
        minSdk = 26
        targetSdk = 35
        versionCode = (System.getenv("GITHUB_RUN_NUMBER") ?: "1").toInt()
        versionName = "0.1.${System.getenv("GITHUB_RUN_NUMBER") ?: "0"}"
        ndk { abiFilters += listOf("arm64-v8a") }
    }

    signingConfigs {
        create("ci") {
            val ks = System.getenv("QT_KEYSTORE_FILE")
            if (ks != null && file(ks).exists()) {
                storeFile = file(ks)
                storePassword = System.getenv("QT_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("QT_KEY_ALIAS")
                keyPassword = System.getenv("QT_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        debug {
            // a stable key (from CI) lets new builds install as updates without losing app data
            val ci = signingConfigs.getByName("ci")
            if (ci.storeFile != null) signingConfig = ci
        }
        release {
            isMinifyEnabled = false
            val ci = signingConfigs.getByName("ci")
            signingConfig = if (ci.storeFile != null) ci else null
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    lint {
        abortOnError = false
        checkReleaseBuilds = false
    }
}

chaquopy {
    defaultConfig {
        version = "3.10"
        // Android builds from the Chaquopy repository (see docs/ANDROID.md for why these versions)
        pip {
            install("numpy==1.23.3")
            install("scipy==1.8.1")
            install("pandas==2.1.3")
            install("statsmodels==0.14.5")
            install("cryptography==42.0.8")
            install("pydantic==1.10.21")
            install("starlette==0.46.2")
            install("uvicorn==0.34.3")
            install("wsproto==1.2.0")
            install("httpx==0.28.1")
        }
        pyc { src = true }
    }
    sourceSets {
        getByName("main") { srcDir(pyStage) }
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.webkit:webkit:1.12.1")
}

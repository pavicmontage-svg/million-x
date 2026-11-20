plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
    id("com.chaquo.python")
}

// Firebase push is enabled only when app/google-services.json exists
// (download it from the Firebase console for package com.millionx.ai).
if (file("google-services.json").exists()) {
    apply(plugin = "com.google.gms.google-services")
}

android {
    namespace = "com.millionx.ai"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.millionx.ai"
        minSdk = 30
        targetSdk = 35
        versionCode = 4
        versionName = "1.3.0"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        // arm64-v8a = real phones (S25 Ultra); x86_64 = emulator for the UI tests.
        // CI builds the phone APK with -PmxAbis=arm64-v8a: the embedded Python makes a
        // two-ABI APK larger than GitHub's 100 MB file limit.
        val abis = (project.findProperty("mxAbis") as String? ?: "arm64-v8a,x86_64").split(",").map { it.trim() }
        ndk { abiFilters += abis }
    }

    signingConfigs {
        create("release") {
            val ks = System.getenv("MX_KEYSTORE_FILE")
            if (ks != null && file(ks).exists()) {
                storeFile = file(ks)
                storePassword = System.getenv("MX_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("MX_KEY_ALIAS")
                keyPassword = System.getenv("MX_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            // Production releases are signed ONLY with the owner's key. Without it the
            // release APK stays unsigned and must not be distributed.
            val rel = signingConfigs.getByName("release")
            signingConfig = if (rel.storeFile != null) rel else null
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    buildFeatures { compose = true }
    lint {
        abortOnError = false
        checkReleaseBuilds = false
    }
}

// The complete Python backend (trading engine + API) is embedded in the app and runs
// in a foreground service on the phone. Versions are the Android builds available
// in the Chaquopy package repository for Python 3.12.
chaquopy {
    defaultConfig {
        version = "3.12"
        pip {
            install("numpy==1.26.2")
            install("cryptography==42.0.8")
            install("starlette>=0.40")
            install("uvicorn>=0.30")
            install("wsproto>=1.2")
            install("requests>=2.31")
        }
    }
    sourceSets {
        getByName("main") {
            srcDir("../../backend")
        }
    }
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2024.12.01")
    implementation(composeBom)
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("androidx.activity:activity-compose:1.9.3")
    implementation("androidx.fragment:fragment-ktx:1.8.5")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.7")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.7")
    implementation("androidx.lifecycle:lifecycle-process:2.8.7")
    implementation("androidx.navigation:navigation-compose:2.8.5")
    implementation("androidx.biometric:biometric:1.1.0")
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.7.3")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")
    implementation(platform("com.google.firebase:firebase-bom:33.7.0"))
    implementation("com.google.firebase:firebase-messaging")
    debugImplementation("androidx.compose.ui:ui-tooling")

    testImplementation("junit:junit:4.13.2")
    androidTestImplementation(composeBom)
    androidTestImplementation("androidx.compose.ui:ui-test-junit4")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test:runner:1.6.2")
    debugImplementation("androidx.compose.ui:ui-test-manifest")
}

tasks.withType<Test>().configureEach {
    testLogging {
        events("passed", "skipped", "failed")
        showStandardStreams = false
    }
}
